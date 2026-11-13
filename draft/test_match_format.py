"""match_when_ready.sh says which format it is about to start. Run: python3 -m unittest test_match_format -v   (or python3 draft/test_match_format.py)

Seen at the table (2026-11-13 retro): the host started ONE four-player table when the players had agreed on 1v1. The script has no default
format, but it never said which it was about to start before it sat waiting, unattended, for the last "I'm ready".

Rehearsal mode only (--dry-run): a scratch LIVE_DIR, a draft port nothing listens on, and --wait-secs so it gives up; no table, no tunnel and
no draft server is started or contacted beyond that dead loopback port. Before the change there is no FORMAT line (the two format tests fail)."""
from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "match_when_ready.sh"
LIVE_PORTS = {8800, 8801, 8820, 8830, 8883}


def dead_port() -> int:
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if p not in LIVE_PORTS:
            return p


def run(*args):
    with tempfile.TemporaryDirectory(prefix="mwr-test-") as live:
        env = {**os.environ, "LIVE_DIR": live, "PLAY_ROUND": "/usr/bin/false", "PLAY_FOUR": "/usr/bin/false"}
        r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=60, env=env)
        return r.returncode, r.stdout + r.stderr


class Format(unittest.TestCase):
    def test_no_format_is_refused(self):
        rc, out = run()
        self.assertEqual(rc, 1)
        self.assertIn("usage", out)
        self.assertNotIn("FORMAT:", out)

    def test_pairings_are_announced_before_the_wait(self):
        rc, out = run("--dry-run", "--draft-port", str(dead_port()), "--wait-secs", "1", "--poll", "1", "Michael:Sam", "Claude:Fusion")
        self.assertEqual(rc, 2, out)                                   # gave up waiting: nothing was started
        self.assertIn("FORMAT: TWO 1v1 tables: Michael vs Sam on :8861, and Claude vs Fusion on :8862.", out)
        self.assertIn("^C now", out)
        self.assertLess(out.index("FORMAT:"), out.index("gave up waiting"))
        self.assertNotIn("STARTING:", out)

    def test_four_is_announced_as_one_table(self):
        rc, out = run("--dry-run", "--four", "--draft-port", str(dead_port()), "--wait-secs", "1", "--poll", "1")
        self.assertEqual(rc, 2, out)
        self.assertIn("FORMAT: ONE four-player table (free-for-all): Michael, Sam, Claude, Fusion on :8861.", out)
        self.assertNotIn("STARTING:", out)


if __name__ == "__main__":
    unittest.main()
