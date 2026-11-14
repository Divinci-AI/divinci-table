"""The match score beside "Join the match" (2026-11-14: a best-two-out-of-three match plays on one table; the draft page says where it stands).
Run: python3 -m unittest test_match_score -v   (or python3 draft/test_match_score.py)

The host's script (match_when_ready.sh --follow) sends {"scores": {player: "Sam leads 1–0"}} to the same host-only route that publishes the
links. Each player's own /api/match then carries `score`. Nothing here changes who may publish, or what a link may be."""
from __future__ import annotations

import json
import unittest

import test_ready as T

S, MICHAEL_LINK, SAM_LINK = T.S, T.MICHAEL_LINK, T.SAM_LINK


class ScoreTests(T.Base):
    def score(self, scores, **kw):
        return self.req("POST", "/api/match/publish", {"scores": scores}, **kw)

    def mine(self, who):
        return self.req("GET", "/api/match", token=who)[1]

    def test_a_score_needs_a_published_match(self):
        self.assertEqual(self.score({"Sam": "Sam leads 1–0"})[0], 409)
        self.assertEqual(self.mine("Sam")["score"], "")

    def test_each_player_gets_their_own_tables_score_and_the_link_stays(self):
        self.publish()
        self.assertEqual(self.mine("Sam")["score"], "")
        code, out, raw = self.score({"Michael": "Sam leads 1–0", "sam": "Sam leads 1–0"})
        self.assertEqual((code, out["published"]), (200, ["Michael", "Sam"]))
        self.assertNotIn("key=", raw)                                # the reply still carries names only
        for who, link in (("Michael", MICHAEL_LINK), ("Sam", SAM_LINK)):
            m = self.mine(who)
            self.assertEqual((m["score"], m["link"], m["status"]), ("Sam leads 1–0", link, "ready"))
        self.assertEqual(self.mine("Claude")["score"], "")           # not at that table: nothing

    def test_a_later_score_replaces_it_and_an_empty_one_removes_it(self):
        self.publish()
        self.score({"Sam": "Sam leads 1–0"})
        self.score({"Sam": "Michael wins the match 2–1"})
        self.assertEqual(self.mine("Sam")["score"], "Michael wins the match 2–1")
        self.score({"Sam": ""})
        self.assertEqual(self.mine("Sam")["score"], "")

    def test_a_republished_link_keeps_the_score_and_clear_removes_both(self):
        self.publish()
        self.score({"Sam": "Tied 1–1"})
        self.publish({"Michael": MICHAEL_LINK, "Sam": SAM_LINK})     # the tunnel moved: --follow re-publishes the links
        self.assertEqual(self.mine("Sam")["score"], "Tied 1–1")
        self.assertEqual(self.req("POST", "/api/match/publish", {"clear": True})[0], 200)
        self.assertEqual((self.mine("Sam")["score"], self.mine("Sam")["link"]), ("", None))

    def test_only_plain_short_text_is_a_score(self):
        self.publish()
        for bad in ("x" * 81, "a\x00b", "a\x1b[31mb", 7, None, ["Sam leads"]):
            self.score({"Sam": "Sam leads 1–0"})
            self.assertEqual(self.score({"Sam": bad})[0], 200, repr(bad))
            self.assertEqual(self.mine("Sam")["score"], "", repr(bad))  # not a score: the old one is removed, nothing odd is shown
        self.score({"Sam": "  Sam   leads\n1–0 "})
        self.assertEqual(self.mine("Sam")["score"], "Sam leads 1–0")     # white space is tidied

    def test_an_unknown_player_or_a_bad_body_is_refused(self):
        self.publish()
        self.assertEqual(self.score({"Mallory": "x"})[0], 400)
        self.assertEqual(self.score(["Sam"])[0], 400)
        self.assertEqual(self.mine("Sam")["score"], "")

    def test_a_score_is_host_only(self):
        self.publish()
        for tok in (None, "Sam", "Claude"):
            code, _, _ = self.score({"Sam": "Sam wins the match 2–0"}, headers={"CF-Connecting-IP": "203.0.113.9"}, token=tok)
            self.assertEqual(code, 403, tok)
        self.assertEqual(self.mine("Sam")["score"], "")

    def test_the_score_survives_a_restart(self):
        self.publish()
        self.score({"Sam": "Sam leads 1–0"})
        self.assertEqual(json.loads((self.live / "draft-match.json").read_text())["scores"], {"Sam": "Sam leads 1–0"})
        self.start(keep=True)
        self.assertEqual(self.mine("Sam")["score"], "Sam leads 1–0")

    def test_the_page_shows_it_escaped_beside_the_button(self):
        self.assertIn('Join the match</button>${MATCH.score?`<span class="m" id="rdyscore">Match: <b>${esc(MATCH.score)}</b></span>`:""}', S.PAGE)


if __name__ == "__main__":
    unittest.main()
