"""match_when_ready.sh and a best-two-out-of-three match (2026-11-14). Run: python3 draft/test_match_follow.py

Rehearsal mode only (--dry-run): a scratch LIVE_DIR, a stand-in draft server and stand-in tables on free loopback ports, a stand-in play
script. Nothing real is started or contacted. Proved:
  * a 1v1 round is started as MATCHES (TABLE_MATCH=bo3 reaches the play script, which is what starts table/server.py); --single and --four
    start single games; --countdown is passed on;
  * --follow prints every game's result and every match's result as the tables report them, puts the score beside each human's Join the
    match button (never an agent's, never a link in the output), and exits 0 by itself when every table has finished."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "match_when_ready.sh"
KINDS = {"Michael": "human", "Sam": "human", "Claude": "agent", "Fusion": "agent"}
SAM_LINK = "https://quiet-river.trycloudflare.com/me?player=Sam#key=SAMSECRETKEY"


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


def draft_server(posts):
    class H(Quiet):
        def do_GET(self):
            if self.path.startswith("/api/ready/state"):
                return self.reply(200, {"draft_done": True, "all_ready": True, "players": {n: {"kind": k, "ready": True, "total": 40} for n, k in KINDS.items()}})
            self.reply(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            posts.append(body)
            self.reply(200, {"ok": True, "published": sorted(body.get("links") or body.get("scores") or [])})
    return serve(H)


def table_server(state):
    """A stand-in table: GET /api/match answers whatever `state["match"]` holds now."""
    class H(Quiet):
        def do_GET(self):
            if self.path.startswith("/api/match"):
                return self.reply(200, state["match"])
            if self.path.startswith("/api/game/summary"):
                return self.reply(200, state.get("summary") or {"over": False})
            self.reply(404, {})
    return serve(H)


def game(n, winner, turns, a, b, la, lb):
    return {"n": n, "winner": winner, "draw": winner is None, "turns": turns, "totals": {a: la, b: lb}}


class Follow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mwr-follow-")
        self.live = Path(self.tmp.name)
        self.posts, self.ta, self.tb = [], {"match": {"on": True, "games": [], "over": False, "score": "No games won yet", "next": None}}, {"match": {"on": True, "games": [], "over": False, "score": "No games won yet", "next": None}}
        self.servers = [draft_server(self.posts), table_server(self.ta), table_server(self.tb)]
        self.dport, self.pa, self.pb = (s.server_address[1] for s in self.servers)
        for name, text in (("michael-seat.key", "MICHAELSECRETKEY"), ("sam-link.txt", SAM_LINK)):
            f = self.live / name
            f.write_text(text); f.chmod(0o600)
        self.play_log = self.live / "play-env.txt"
        stub = self.live / "play_stub.sh"
        stub.write_text(f'#!/bin/bash\necho "TABLE_MATCH=${{TABLE_MATCH:-unset}} TABLE_MATCH_COUNTDOWN=${{TABLE_MATCH_COUNTDOWN:-unset}} args=$*" > "{self.play_log}"\n'
                        f'touch "{self.live}/michael-seat.key" "{self.live}/sam-link.txt"\n')
        stub.chmod(0o755)
        tool = self.live / "deck_tool.py"
        tool.write_text("import sys\nopen(sys.argv[2], 'w').write('{}')\n")
        for n in KINDS:
            (self.live / f"deck-{n.lower()}.txt").write_text("17 Mountain\n23 Starfleet Security\n")
        self.env = {**os.environ, "LIVE_DIR": str(self.live), "PLAY_ROUND": str(stub), "PLAY_FOUR": str(stub), "DECK_TOOL": str(tool), "FOLLOW_POLL": "0.3"}

    def tearDown(self):
        for s in self.servers:
            s.shutdown(); s.server_close()
        self.tmp.cleanup()

    def run_script(self, *args, timeout=60):
        r = subprocess.run(["bash", str(SCRIPT), "--dry-run", "--draft-port", str(self.dport), *args], capture_output=True, text=True, timeout=timeout, env=self.env)
        return r.returncode, r.stdout + r.stderr

    def test_a_round_is_started_as_matches_by_default(self):
        rc, out = self.run_script("--ports", f"{self.pa},{self.pb}", "Michael:Sam", "Claude:Fusion")
        self.assertEqual(rc, 0, out)
        self.assertIn("TABLE_MATCH=bo3 TABLE_MATCH_COUNTDOWN=unset", self.play_log.read_text())
        self.assertIn("Each is a MATCH: best two out of three, the next game starting by itself on the same table", out)
        self.assertIn("published the match link for: Michael, Sam", out)

    def test_single_starts_single_games_and_countdown_is_passed_on(self):
        rc, out = self.run_script("--single", "--ports", f"{self.pa},{self.pb}", "Michael:Sam", "Claude:Fusion")
        self.assertEqual(rc, 0, out)
        self.assertIn("TABLE_MATCH=single", self.play_log.read_text())
        self.assertIn("ONE game each (--single)", out)
        rc, out = self.run_script("--countdown", "40", "--ports", f"{self.pa},{self.pb}", "Michael:Sam", "Claude:Fusion")
        self.assertIn("TABLE_MATCH=bo3 TABLE_MATCH_COUNTDOWN=40", self.play_log.read_text())
        self.assertEqual(self.run_script("--countdown", "soon", "Michael:Sam", "Claude:Fusion")[0], 1)

    def test_four_players_is_never_a_match(self):
        rc, out = self.run_script("--four", "--port", str(self.pa))
        self.assertEqual(rc, 0, out)
        self.assertIn("TABLE_MATCH=single", self.play_log.read_text())
        self.assertNotIn("MATCH:", out)

    def test_follow_reports_every_game_and_match_and_then_exits(self):
        p = subprocess.Popen(["bash", str(SCRIPT), "--dry-run", "--publish-only", "--follow", "--draft-port", str(self.dport), "--ports", f"{self.pa},{self.pb}",
                              "Michael:Sam", "Claude:Fusion"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=self.env)
        try:
            time.sleep(1.5)
            self.assertIsNone(p.poll(), "it keeps following while the matches are being played")
            self.ta["match"] = {"on": True, "games": [game(1, "Sam", 9, "Michael", "Sam", 0, 12)], "over": False, "score": "Sam leads 1–0", "next": {"n": 2, "chooser": "Michael"}}
            time.sleep(1.2)
            self.tb["match"] = {"on": True, "games": [game(1, "Claude", 7, "Claude", "Fusion", 3, -1), game(2, "Claude", 6, "Claude", "Fusion", 20, 0)], "over": True, "winner": "Claude",
                                "score": "Claude wins the match 2–0", "next": None}
            time.sleep(1.2)
            self.assertIsNone(p.poll(), "one table's match being over does not end the follow")
            self.ta["match"] = {"on": True, "games": [game(1, "Sam", 9, "Michael", "Sam", 0, 12), game(2, None, 14, "Michael", "Sam", 0, 0), game(3, "Michael", 11, "Michael", "Sam", 4, -2)],
                                "over": False, "score": "Tied 1–1", "next": {"n": 4, "chooser": "Sam"}}
            time.sleep(1.2)
            self.ta["match"] = {**self.ta["match"], "games": self.ta["match"]["games"] + [game(4, "Michael", 8, "Michael", "Sam", 9, 0)], "over": True, "winner": "Michael",
                                "score": "Michael wins the match 2–1", "next": None}
            out, _ = p.communicate(timeout=20)
        finally:
            if p.poll() is None:
                p.kill()
        self.assertEqual(p.returncode, 0, out)
        a, b = f"table :{self.pa}", f"table :{self.pb}"
        for line in (f"{a}: game 1: Sam wins in 9 turns (Michael 0, Sam 12)", f"{a}: Sam leads 1–0; game 2 starts by itself on the same table (Michael chooses play or draw)",
                     f"{b}: game 1: Claude wins in 7 turns", f"{b}: game 2: Claude wins in 6 turns", f"{b}: MATCH OVER: Claude wins the match 2–0",
                     f"{a}: game 2: a draw in 14 turns", f"{a}: game 3: Michael wins in 11 turns", f"{a}: game 4: Michael wins in 8 turns",
                     f"{a}: MATCH OVER: Michael wins the match 2–1", "every table has finished"):
            self.assertIn(line, out)
        self.assertEqual(out.count("game 1: Sam wins"), 1, "a game is reported once")
        self.assertLess(out.index(f"{a}: game 1"), out.index(f"{b}: MATCH OVER"))
        self.assertLess(out.index(f"{b}: MATCH OVER"), out.index(f"{a}: MATCH OVER"))
        scores = [x["scores"] for x in self.posts if "scores" in x]
        self.assertEqual(scores, [{"Michael": "Sam leads 1–0", "Sam": "Sam leads 1–0"}, {"Michael": "Tied 1–1", "Sam": "Tied 1–1"},
                                  {"Michael": "Michael wins the match 2–1", "Sam": "Michael wins the match 2–1"}],
                         "each human's draft page gets its own table's score after every game; the agents' table sends none")
        self.assertNotIn("SECRETKEY", out)
        self.assertTrue(all("links" in x for x in self.posts[:1]), "the links are published first, as before")

    def test_follow_ends_for_single_game_tables_too(self):
        self.ta["match"] = self.tb["match"] = {"on": False}
        p = subprocess.Popen(["bash", str(SCRIPT), "--dry-run", "--publish-only", "--follow", "--single", "--draft-port", str(self.dport), "--ports", f"{self.pa},{self.pb}",
                              "Michael:Sam", "Claude:Fusion"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=self.env)
        try:
            time.sleep(1.2)
            self.assertIsNone(p.poll())
            self.ta["summary"] = {"over": True, "winner": "Sam", "summary": {"turns": 9}}
            self.tb["summary"] = {"over": True, "winner": "Fusion", "summary": {"turns": 12}}
            out, _ = p.communicate(timeout=20)
        finally:
            if p.poll() is None:
                p.kill()
        self.assertEqual(p.returncode, 0, out)
        self.assertIn(f"table :{self.pa} (Michael vs Sam) plays a single game", out)
        self.assertIn(f"table :{self.pa}: GAME OVER: Sam wins in 9 turns", out)
        self.assertIn(f"table :{self.pb}: GAME OVER: Fusion wins in 12 turns", out)
        self.assertFalse([x for x in self.posts if "scores" in x])


if __name__ == "__main__":
    unittest.main()
