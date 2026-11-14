"""draft/match_when_ready.sh starts a round of any size, never waits for a seat the autopilot plays, and never publishes an unproved link
(retro M4, M5, M3; 2026-11-14).

    python3 launch/test_match_round.py     (rehearsal mode: a scratch live folder, stand-in draft server, tables, play script and edge)

  1. one pairing, and three, are a round (it used to be exactly two); the play script gets every pairing and one port each;
  2. `Name=bot`: the script does not wait for that seat's "I'm ready", passes the pairing on as written, and publishes no link for it;
  3. the remote player's link is published only if its address answers from HIS table now (launch/tunnels.py check):
     a live address -> published; a dead address (as on 2026-11-13) -> NOT published, exit 4, and it says why; the local player's
     link is still published; the link and the key are never in the output.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import fake_edge  # noqa: E402

SCRIPT = HERE.parent / "draft" / "match_when_ready.sh"
HOST = "quiet-river-test.trycloudflare.com"
SAM_LINK = f"https://{HOST}/me?player=Sam#key=SAMSECRETKEY"


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Quiet(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers()
        self.wfile.write(raw)


def draft_server(posts, ready):
    class H(Quiet):
        def do_GET(self):
            if self.path.startswith("/api/ready/state"):
                return self.reply(200, {"draft_done": True, "all_ready": all(ready.values()),
                                        "players": {n: {"kind": "agent" if n in ("Claude", "Fusion") else "human", "ready": r, "total": 40} for n, r in ready.items()}})
            self.reply(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            posts.append(body)
            self.reply(200, {"ok": True, "published": sorted(body.get("links") or body.get("scores") or [])})
    return serve(H)


def table_server(word):
    """A stand-in table: its own word at /api/instance, a match that never ends at /api/match."""
    class H(Quiet):
        def do_GET(self):
            if self.path.startswith("/api/instance"):
                return self.reply(200, {"instance": word})
            if self.path.startswith("/api/match"):
                return self.reply(200, {"on": True, "games": [], "over": False, "score": "No games won yet", "next": None})
            self.reply(404, {})
    return serve(H)


class Round(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mwr-round-")
        self.live = Path(self.tmp.name) / "live"
        self.edge_dir = Path(self.tmp.name) / "edge"
        self.live.mkdir(); self.edge_dir.mkdir()
        self.posts, self.ready = [], {n: True for n in ("Michael", "Sam", "Claude", "Fusion", "Ann", "Ben")}
        self.tables = [table_server(f"word-{i}") for i in range(3)]
        self.draft = draft_server(self.posts, self.ready)
        self.edge = fake_edge.serve(self.edge_dir)
        self.dport = self.draft.server_address[1]
        self.ports = [t.server_address[1] for t in self.tables]
        self.play_log = self.live / "play-args.txt"
        stub = self.live / "play_stub.sh"
        stub.write_text(f'#!/bin/bash\necho "args=$*" > "{self.play_log}"\n'
                        f'for n in michael sam ann ben; do printf %s "$(echo $n | tr a-z A-Z)SECRETKEY" > "{self.live}/$n-seat.key"; chmod 600 "{self.live}/$n-seat.key"; done\n'
                        f'[ "${{STUB_NO_LINK:-0}}" = 1 ] || {{ printf "%s\\n" "{SAM_LINK}" > "{self.live}/sam-link.txt"; chmod 600 "{self.live}/sam-link.txt"; }}\n')
        stub.chmod(0o755)
        tool = self.live / "deck_tool.py"
        tool.write_text("import sys\nopen(sys.argv[2], 'w').write('{}')\n")
        for n in self.ready:
            (self.live / f"deck-{n.lower()}.txt").write_text("17 Mountain\n23 Starfleet Security\n")
        self.env = {**os.environ, "LIVE_DIR": str(self.live), "PLAY_ROUND": str(stub), "PLAY_FOUR": str(stub), "DECK_TOOL": str(tool), "FOLLOW_POLL": "0.3",
                    "TUNNEL_PROBE_VIA": f"http://127.0.0.1:{self.edge.server_address[1]}", "TUNNELS": str(HERE / "tunnels.py")}
        self.env.pop("PROVE_LINKS", None)

    def tearDown(self):
        for s in [*self.tables, self.draft, self.edge]:
            s.shutdown(); s.server_close()
        self.tmp.cleanup()

    def reach(self, port):
        """The tunnel address of Sam's link leads to the table on `port` (None: to nothing, a dead tunnel)."""
        f = self.edge_dir / f"{HOST}.json"
        if port is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(json.dumps({"port": port}))

    def run_script(self, *args, timeout=60, **extra):
        r = subprocess.run(["bash", str(SCRIPT), "--dry-run", "--draft-port", str(self.dport), "--wait-secs", "3", "--poll", "0.2", *map(str, args)],
                           capture_output=True, text=True, timeout=timeout, env={**self.env, **extra})
        return r.returncode, r.stdout + r.stderr

    def links(self):
        return next((b["links"] for b in reversed(self.posts) if b.get("links")), {})

    def test_one_pairing_is_a_round(self):
        self.reach(self.ports[0])
        rc, out = self.run_script("--ports", self.ports[0], "Michael:Sam")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"FORMAT: ONE 1v1 table: Michael vs Sam on :{self.ports[0]}. It is a MATCH", out)
        self.assertEqual(self.play_log.read_text().strip(), f"args=--ports {self.ports[0]} Michael:Sam")
        self.assertIn("published the match link for: Michael, Sam", out)

    def test_three_pairings_are_a_round(self):
        self.reach(self.ports[0])
        rc, out = self.run_script("--ports", ",".join(map(str, self.ports)), "Michael:Sam", "Claude:Fusion", "Ann:Ben")
        self.assertEqual(rc, 0, out)
        p = self.ports
        self.assertIn(f"FORMAT: THREE 1v1 tables: Michael vs Sam on :{p[0]}, Claude vs Fusion on :{p[1]}, and Ann vs Ben on :{p[2]}. Each is a MATCH", out)
        self.assertEqual(self.play_log.read_text().strip(), f"args=--ports {p[0]},{p[1]},{p[2]} Michael:Sam Claude:Fusion Ann:Ben")
        self.assertEqual(sorted(self.links()), ["Ann", "Ben", "Michael", "Sam"])
        self.assertTrue(self.links()["Ann"].startswith(f"http://127.0.0.1:{p[2]}/me?player=Ann#key="))

    def test_usage_and_bad_rounds(self):
        self.assertEqual(self.run_script()[0], 1)
        self.assertIn("usage", self.run_script()[1])
        rc, out = self.run_script("Michael:Sam", "Sam:Claude")
        self.assertEqual(rc, 1)
        self.assertIn("Sam is in two pairings", out)
        rc, out = self.run_script("--ports", self.ports[0], "Michael:Sam", "Claude:Fusion")
        self.assertEqual(rc, 1)
        self.assertIn("one per pairing", out)
        self.assertFalse(self.play_log.exists())

    def test_a_seat_the_autopilot_plays_is_not_waited_for_and_gets_no_link(self):
        self.ready["Sam"] = False                      # Sam went to bed: he never presses "I'm ready"
        rc, out = self.run_script("--ports", self.ports[0], "Michael:Sam")
        self.assertEqual(rc, 2, out)                   # written as a person's seat, the script waits for him (and gives up here)
        self.assertIn("waiting on Sam", out)
        self.assertFalse(self.play_log.exists())
        rc, out = self.run_script("--ports", self.ports[0], "Michael:Sam=bot")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"Michael vs Sam (autopilot) on :{self.ports[0]}", out)
        self.assertIn("everyone is ready: Michael, Sam (autopilot: not waited for)", out)
        self.assertEqual(self.play_log.read_text().strip(), f"args=--ports {self.ports[0]} Michael:Sam=bot")
        self.assertEqual(sorted(self.links()), ["Michael"])
        self.assertIn("published the match link for: Michael", out)
        self.assertNotIn("Sam:", out.split("published")[1])

    def test_a_round_with_no_person_publishes_nothing_and_is_not_an_error(self):
        rc, out = self.run_script("--ports", self.ports[1], "Claude:Fusion")
        self.assertEqual(rc, 0, out)
        self.assertIn("no person plays in this round", out)
        self.assertEqual(self.links(), {})

    def test_the_remote_link_is_published_only_when_its_address_reaches_his_table(self):
        self.reach(self.ports[0])                      # a live tunnel to Sam's table
        rc, out = self.run_script("--ports", f"{self.ports[0]},{self.ports[1]}", "Michael:Sam", "Claude:Fusion")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.links().get("Sam"), SAM_LINK)
        self.assertNotIn("SAMSECRETKEY", out)
        self.assertNotIn(HOST + "/me", out)
        self.posts.clear()
        self.reach(None)                               # the dead address of 2026-11-13: Cloudflare answers 530 for it
        rc, out = self.run_script("--ports", f"{self.ports[0]},{self.ports[1]}", "Michael:Sam", "Claude:Fusion")
        self.assertEqual(rc, 4, out)
        self.assertEqual(sorted(self.links()), ["Michael"], "the dead link must not be published; the local player's still is")
        self.assertIn("NOT published: Sam: the link's address does not reach this table", out)
        self.assertIn("answers 530", out)
        self.assertNotIn("SAMSECRETKEY", out)
        self.posts.clear()
        self.reach(self.ports[1])                      # the address leads to the OTHER table
        rc, out = self.run_script("--ports", f"{self.ports[0]},{self.ports[1]}", "Michael:Sam", "Claude:Fusion")
        self.assertEqual(rc, 4, out)
        self.assertEqual(sorted(self.links()), ["Michael"])
        self.assertIn("ANOTHER table", out)

    def test_a_launcher_that_could_not_prove_the_tunnel_still_gets_the_local_link_published(self):
        rc, out = self.run_script("--ports", self.ports[0], "Michael:Sam", STUB_NO_LINK="1")
        self.assertEqual(rc, 4, out)
        self.assertEqual(sorted(self.links()), ["Michael"])
        self.assertIn("NOT published: Sam: sam-link.txt does not exist", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
