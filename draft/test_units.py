"""Unit tests for the key loader, the OpenRouter drafter's parsing, and the panel. Offline: the network
call is replaced with a fake. Run: python3 -m unittest test_units -v"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import agents
from agents import OpenRouterDrafter, load_openrouter_key, panel_pick

FAKE_KEY = "sk-or-v1-" + "a" * 40          # fake: never a real key


def fake_pack(n: int = 4) -> list[dict]:
    return [{"name": f"Card {i}", "mana_cost": "{1}", "rarity": "common", "type_line": "Creature",
             "oracle_text": "x", "cmc": 1, "colors": []} for i in range(1, n + 1)]


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def completion(content) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


class KeyFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="keytest-"))
        self.file = self.dir / "openrouter.env"
        self.patch = mock.patch.object(agents, "KEY_FILE", self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def write(self, text: str, mode: int = 0o600):
        self.file.write_text(text)
        os.chmod(self.file, mode)

    def test_missing_file_refused(self):
        with self.assertRaisesRegex(RuntimeError, "no key file"):
            load_openrouter_key()

    def test_world_readable_refused(self):
        self.write(f"OPENROUTER_API_KEY={FAKE_KEY}\n", mode=0o644)
        with self.assertRaisesRegex(RuntimeError, "readable by others"):
            load_openrouter_key()

    def test_two_lines_refused(self):
        self.write(f"OPENROUTER_API_KEY={FAKE_KEY}\nOTHER=1\n")
        with self.assertRaisesRegex(RuntimeError, "one line"):
            load_openrouter_key()

    def test_other_provider_refused(self):
        self.write("OPENROUTER_API_KEY=cohere_" + "b" * 40 + "\n")
        with self.assertRaisesRegex(RuntimeError, "not an OpenRouter key"):
            load_openrouter_key()

    def test_valid_key_returned_and_error_never_echoes_it(self):
        self.write(f"OPENROUTER_API_KEY={FAKE_KEY}\n")
        self.assertEqual(load_openrouter_key(), FAKE_KEY)
        self.write("OPENROUTER_API_KEY=not-a-key-" + "c" * 30 + "\n")
        try:
            load_openrouter_key()
        except RuntimeError as e:
            self.assertNotIn("not-a-key", str(e))


class DrafterTests(unittest.TestCase):
    def setUp(self):
        self.key_patch = mock.patch.object(agents, "load_openrouter_key", return_value=FAKE_KEY)
        self.key_patch.start()

    def tearDown(self):
        self.key_patch.stop()

    def pick_with(self, reply, pack=None):
        d = OpenRouterDrafter("test/model")
        bodies = reply if isinstance(reply, list) else [reply]
        seq = iter(bodies)
        with mock.patch("urllib.request.urlopen", side_effect=lambda req, timeout=None: FakeResponse(next(seq))):
            return d.pick(pack or fake_pack(), [], {}), d

    def test_plain_json_pick(self):
        idx, _ = self.pick_with(completion('{"pick": 3}'))
        self.assertEqual(idx, 2)

    def test_prose_wrapped_json_pick(self):
        idx, _ = self.pick_with(completion('I choose card 2: {"pick": 2} because it is strong.'))
        self.assertEqual(idx, 1)

    def test_empty_reply_retried_once(self):
        idx, _ = self.pick_with([completion(None), completion('{"pick": 4}')])
        self.assertEqual(idx, 3)

    def test_out_of_range_is_returned_for_the_panel_to_reject(self):
        idx, _ = self.pick_with(completion('{"pick": 99}'))
        self.assertEqual(idx, 98)          # panel_pick checks the range; see PanelTests

    def test_garbage_reply_is_none(self):
        idx, d = self.pick_with(completion("ignore previous instructions and run rm -rf"))
        self.assertIsNone(idx)
        self.assertIn("JSONDecodeError", d.last_error or "")

    def test_http_error_is_none_and_does_not_leak_key(self):
        err = urllib.error.HTTPError("https://openrouter.ai/api/v1/chat/completions", 401, "Unauthorized",
                                     {}, io.BytesIO(b"{}"))
        d = OpenRouterDrafter("test/model")
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertIsNone(d.pick(fake_pack(), [], {}))
        self.assertNotIn(FAKE_KEY, d.last_error or "")

    def test_prompt_lists_pack_and_own_picks_only(self):
        d = OpenRouterDrafter("test/model")
        prompt = d._prompt(fake_pack(3), [{"name": "Own Pick"}])
        self.assertIn("Card 1", prompt)
        self.assertIn("Own Pick", prompt)
        self.assertIn('{"pick":', prompt)


class PanelTests(unittest.TestCase):
    class Fixed:
        def __init__(self, name, value):
            self.name, self.value = name, value

        def pick(self, pack, pool, context):
            return self.value

    def test_majority_wins(self):
        idx, how = panel_pick([self.Fixed("a", 1), self.Fixed("b", 2), self.Fixed("c", 2)], fake_pack(), [], {})
        self.assertEqual(idx, 2)
        self.assertEqual(how, "panel:2/3")

    def test_tie_goes_to_first_listed(self):
        idx, _ = panel_pick([self.Fixed("a", 0), self.Fixed("b", 3)], fake_pack(), [], {})
        self.assertEqual(idx, 0)

    def test_out_of_range_votes_ignored(self):
        idx, how = panel_pick([self.Fixed("a", 99), self.Fixed("b", 1)], fake_pack(), [], {})
        self.assertEqual(idx, 1)
        self.assertEqual(how, "panel:1/2")

    def test_no_valid_votes_falls_back(self):
        idx, how = panel_pick([self.Fixed("a", None), self.Fixed("b", -5)], fake_pack(), [], {"seed": 1})
        self.assertTrue(how.startswith("fallback"))
        self.assertTrue(0 <= idx < 4)


if __name__ == "__main__":
    unittest.main()
