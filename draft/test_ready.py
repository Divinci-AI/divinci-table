"""Tests for the "I'm ready" step and the match handoff in draft_server.py. Offline; a real server on an ephemeral loopback port with
LIVE pointed at a scratch folder. Run: python3 -m unittest test_ready -v

They cover: the ready flag (legal deck only, cleared by an edit, persisted, old state files still load), agents readying with their own
token, the host-only routes refusing anything that looks tunnelled (every PROXY_HEADERS header, a non-loopback Host, an Origin, even with
a valid player token), the link allowlist, and that a player is only ever handed their own link."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import deckbuild
import draft_banter
import draft_server as S

MICHAEL_LINK = "http://127.0.0.1:18800/me?player=Michael#key=MICHAEL-SECRET-KEY-AAAA"
SAM_LINK = "https://quiet-river-1234.trycloudflare.com/me?player=Sam#key=SAM-SECRET-KEY-BBBB"
SECRETS = ("MICHAEL-SECRET-KEY-AAAA", "SAM-SECRET-KEY-BBBB", "quiet-river-1234")


class Base(unittest.TestCase):
    SEED = 7

    @classmethod
    def setUpClass(cls):
        cls.saved_names = (dict(S.HUMANS), dict(S.AGENTS), dict(S.NAMES), set(S.REQUIRED))
        cls._p = [mock.patch.object(draft_banter.HostVoice, "speak", lambda *a, **k: None),
                  mock.patch.object(draft_banter.Banter, "event", lambda *a, **k: None),
                  mock.patch.object(draft_banter.Banter, "_queue", lambda *a, **k: "off")]
        for p in cls._p:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls._p:
            p.stop()

    def setUp(self):
        for d, v in zip((S.HUMANS, S.AGENTS, S.NAMES), self.saved_names[:3]):
            d.clear(); d.update(v)
        S.REQUIRED.clear(); S.REQUIRED.update(self.saved_names[3])
        self.tmp = tempfile.TemporaryDirectory(prefix="ready-test-")
        self.live = Path(self.tmp.name)
        self._live = mock.patch.object(S, "LIVE", self.live)
        self._live.start()
        self.servers = []
        self.dr = None
        self.start(finish=True)

    def tearDown(self):
        for srv in self.servers:
            srv.shutdown(); srv.server_close()
        self._live.stop()
        self.tmp.cleanup()
        for d, v in zip((S.HUMANS, S.AGENTS, S.NAMES), self.saved_names[:3]):
            d.clear(); d.update(v)
        S.REQUIRED.clear(); S.REQUIRED.update(self.saved_names[3])

    # ---- a draft and a server for it
    def start(self, finish=False, keep=False, seed=None):
        for srv in self.servers:
            srv.shutdown(); srv.server_close()
        self.servers = []
        seed = seed or self.SEED
        dr = S.Draft(seed, self.live / "draft-links.txt" if keep else None)
        if keep:
            dr.restore(self.live / "draft-state.json")
        elif finish:
            while not dr.done:
                for seat in S.NAMES:
                    if seat not in dr.pending:
                        dr.pick(seat, dr.current[seat][0]["id"])
        (self.live / "draft-links.txt").write_text("".join(f"{S.NAMES[s]}: /?k={t}\n" for t, s in dr.tokens.items() if s in S.HUMANS))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(dr))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        self.dr, self.port = dr, srv.server_address[1]
        self.tok = {S.NAMES[s]: t for t, s in dr.tokens.items()}
        return dr

    def req(self, method, path, body=None, headers=None, host=None, token=None, ctype="application/json"):
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if body is not None:
            h["Content-Type"] = ctype
        h.update(headers or {})
        if token:
            path += ("&" if "?" in path else "?") + "k=" + self.tok[token]
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=json.dumps(body) if body is not None and not isinstance(body, bytes) else body, headers=h)
        r = c.getresponse()
        raw = r.read().decode()
        c.close()
        try:
            return r.status, json.loads(raw), raw
        except ValueError:
            return r.status, None, raw

    def deck(self, who):
        v = self.dr.deck_view(next(s for s, n in S.NAMES.items() if n == who))
        return {"main": dict(v["main"]), "basics": dict(v["basics"])}

    def ready(self, who, flag=True):
        return self.req("POST", "/api/ready", {"ready": flag}, token=who)

    def publish(self, links=None, **kw):
        return self.req("POST", "/api/match/publish", {"links": links or {"Michael": MICHAEL_LINK, "Sam": SAM_LINK}, "note": "table 1"}, **kw)

    def ready_everyone(self):
        for who in S.NAMES.values():
            self.assertEqual(self.ready(who)[0], 200, who)


class ReadyFlagTests(Base):
    def test_not_ready_by_default_and_the_strip_lists_all_four(self):
        code, d, _ = self.req("GET", "/api/state", token="Michael")
        self.assertTrue(d["done"])
        self.assertFalse(d["deck"]["ready_flag"])
        self.assertEqual([(p["name"], p["kind"], p["ready"]) for p in d["ready"]],
                         [("Michael", "human", False), ("Sam", "human", False), ("Claude", "agent", False), ("Fusion", "agent", False)])

    def test_only_a_legal_size_deck_can_ready(self):
        dk = self.deck("Michael")
        dk["basics"]["Plains"] = dk["basics"].get("Plains", 0) + 1                  # 41 cards
        self.assertEqual(self.req("POST", "/api/deck", dk, token="Michael")[0], 200)
        code, d, _ = self.ready("Michael")
        self.assertEqual(code, 400)
        self.assertIn("exactly 40", d["error"])
        dk["basics"]["Plains"] -= 1
        self.req("POST", "/api/deck", dk, token="Michael")
        self.assertEqual(self.ready("Michael")[0], 200)
        self.assertTrue(self.req("GET", "/api/state", token="Michael")[1]["deck"]["ready_flag"])
        self.assertTrue(self.req("GET", "/api/state", token="Sam")[1]["ready"][0]["ready"])     # Sam sees Michael's flag

    def test_unready(self):
        self.ready("Sam")
        code, d, _ = self.ready("Sam", False)
        self.assertEqual((code, d["ready"]), (200, False))

    def test_an_edit_after_readying_cancels_it_but_a_resave_of_the_same_deck_does_not(self):
        self.ready("Michael")
        self.assertEqual(self.req("POST", "/api/deck", self.deck("Michael"), token="Michael")[0], 200)
        self.assertTrue(self.dr.deck_view(0)["ready_flag"], "re-saving an identical deck must keep ready")
        dk = self.deck("Michael")
        dk["basics"]["Island"] = dk["basics"].get("Island", 0) + 1
        self.req("POST", "/api/deck", dk, token="Michael")
        self.assertFalse(self.dr.deck_view(0)["ready_flag"])
        self.assertFalse(self.req("GET", "/api/state", token="Michael")[1]["deck"]["ready_flag"])

    def test_reset_to_suggested_cancels_ready_only_when_it_changes_the_deck(self):
        self.ready("Michael")
        self.req("POST", "/api/deck/reset", {}, token="Michael")                    # still the suggestion: nothing changed
        self.assertTrue(self.dr.deck_view(0)["ready_flag"])
        dk = self.deck("Michael"); dk["basics"]["Swamp"] = dk["basics"].get("Swamp", 0) + 1
        self.req("POST", "/api/deck", dk, token="Michael")
        dk["basics"]["Swamp"] -= 1
        self.req("POST", "/api/deck", dk, token="Michael"); self.ready("Michael")
        dk["basics"]["Swamp"] += 5
        self.req("POST", "/api/deck", dk, token="Michael")                          # edited: ready gone
        self.assertFalse(self.dr.deck_view(0)["ready_flag"])

    def test_ready_before_the_draft_is_over_is_refused(self):
        self.start(finish=False)
        self.assertEqual(self.ready("Michael")[0], 400)

    def test_bad_bodies(self):
        self.assertEqual(self.req("POST", "/api/ready", {"ready": "yes"}, token="Michael")[0], 400)
        self.assertEqual(self.req("POST", "/api/ready", b"not json", token="Michael")[0], 400)
        self.assertEqual(self.req("POST", "/api/ready", {})[0], 403)                          # no token
        self.assertEqual(self.req("POST", "/api/ready?k=nope", {"ready": True})[0], 403)      # wrong token
        self.assertEqual(self.req("POST", "/api/ready", {}, token="Michael")[1]["ready"], True)   # no "ready" key: ready

    def test_agents_ready_with_their_own_token(self):
        for who in ("Claude", "Fusion"):
            self.assertEqual(self.ready(who)[1]["ready"], True)
        st = self.req("GET", "/api/ready/state")[1]["players"]
        self.assertTrue(st["Claude"]["ready"] and st["Fusion"]["ready"])
        self.assertFalse(st["Michael"]["ready"] or st["Sam"]["ready"])

    def test_persisted_and_restored_on_restart(self):
        self.ready("Michael"); self.ready("Claude")
        saved = json.loads((self.live / "draft-decks.json").read_text())
        self.assertEqual(saved["ready"], [0, 2])
        self.start(keep=True)
        self.assertEqual(self.req("GET", "/api/ready/state")[1]["players"]["Michael"]["ready"], True)
        st = self.req("GET", "/api/ready/state")[1]["players"]
        self.assertEqual([n for n, p in st.items() if p["ready"]], ["Michael", "Claude"])

    def test_an_old_state_file_without_ready_still_loads(self):
        f = self.live / "draft-decks.json"
        data = json.loads(f.read_text() or "{}") if f.exists() else {}
        self.req("GET", "/api/state", token="Michael")                                # make the file
        data = json.loads(f.read_text())
        data.pop("ready", None)
        f.write_text(json.dumps(data))
        self.start(keep=True)
        st = self.req("GET", "/api/ready/state")[1]
        self.assertTrue(st["draft_done"])
        self.assertFalse(any(p["ready"] for p in st["players"].values()))
        self.assertEqual(self.ready("Michael")[0], 200)

    def test_a_saved_ready_flag_is_dropped_when_its_deck_is_not_legal_or_not_owned(self):
        self.req("GET", "/api/state", token="Michael")
        f = self.live / "draft-decks.json"
        data = json.loads(f.read_text())
        data["decks"]["0"]["basics"]["Plains"] = data["decks"]["0"]["basics"].get("Plains", 0) + 3     # 43 cards, hand-edited
        data["decks"]["1"]["main"]["not-a-card-id"] = 1                                              # a card Sam never drafted
        data["ready"] = [0, 1, 2]
        f.write_text(json.dumps(data))
        self.start(keep=True)
        st = self.req("GET", "/api/ready/state")[1]["players"]
        self.assertEqual([n for n, p in st.items() if p["ready"]], ["Claude"])

    def test_ready_file_from_another_draft_is_ignored(self):
        self.ready("Michael")
        f = self.live / "draft-decks.json"
        data = json.loads(f.read_text()); data["seed"] = 999; f.write_text(json.dumps(data))
        self.start(keep=True)
        self.assertFalse(any(p["ready"] for p in self.req("GET", "/api/ready/state")[1]["players"].values()))

    def test_a_bot_seat_never_blocks(self):
        S.make_bots(["Sam"])
        self.start(finish=True, seed=11)
        st = self.req("GET", "/api/ready/state")[1]
        self.assertEqual(sorted(st["players"]), ["Claude", "Fusion", "Michael"])
        for who in ("Michael", "Claude", "Fusion"):
            self.ready(who)
        self.assertTrue(self.req("GET", "/api/ready/state")[1]["all_ready"])
        self.assertEqual(self.req("GET", "/api/match", token="Michael")[1]["status"], "starting")


class HostOnlyTests(Base):
    def tunnelled(self):
        """every way a request that came through the tunnel (or a browser page) differs from the host's own script"""
        yield "X-Forwarded-For", {"X-Forwarded-For": "203.0.113.9"}, None
        yield "Forwarded", {"Forwarded": "for=203.0.113.9"}, None
        yield "X-Real-IP", {"X-Real-IP": "203.0.113.9"}, None
        yield "CF-Connecting-IP", {"CF-Connecting-IP": "203.0.113.9"}, None
        yield "True-Client-IP", {"True-Client-IP": "203.0.113.9"}, None
        yield "tunnel Host", {}, "quiet-river-1234.trycloudflare.com"
        yield "rebinding Host", {}, "evil.example.com"
        yield "Origin", {"Origin": "https://evil.example.com"}, None
        yield "tunnel (all of it, as Cloudflare sends it)", {"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9",
                                                            "Origin": "https://quiet-river-1234.trycloudflare.com"}, "quiet-river-1234.trycloudflare.com"

    def test_proxy_header_list_is_what_the_matrix_covers(self):
        covered = {next(iter(h)) for n, h, _ in self.tunnelled() if len(h) == 1 and next(iter(h)) in S.PROXY_HEADERS}
        self.assertEqual(covered, set(S.PROXY_HEADERS), "a header was added to PROXY_HEADERS: add it to this matrix")

    def test_ready_state_is_refused_when_tunnelled(self):
        for label, h, host in self.tunnelled():
            code, body, raw = self.req("GET", "/api/ready/state", headers=h, host=host)
            self.assertEqual(code, 403, label)
            self.assertNotIn("players", raw, label)
            code, _, _ = self.req("GET", "/api/ready/state", headers=h, host=host, token="Michael")   # a player token does not help
            self.assertEqual(code, 403, label + " + token")

    def test_publish_is_refused_when_tunnelled_and_changes_nothing(self):
        for label, h, host in self.tunnelled():
            for tok in (None, "Michael", "Claude"):
                code, _, raw = self.publish(headers=h, host=host, token=tok)
                self.assertEqual(code, 403, f"{label} token={tok}")
                for s in SECRETS:
                    self.assertNotIn(s, raw)
        self.assertIsNone(self.dr.match)
        self.assertFalse((self.live / "draft-match.json").exists())
        self.assertEqual(self.req("GET", "/api/match", token="Michael")[1]["link"], None)

    def test_clear_is_host_only_too(self):
        self.assertEqual(self.publish()[0], 200)
        code, _, _ = self.req("POST", "/api/match/publish", {"clear": True}, headers={"CF-Connecting-IP": "203.0.113.9"})
        self.assertEqual(code, 403)
        self.assertIsNotNone(self.dr.match)
        self.assertEqual(self.req("POST", "/api/match/publish", {"clear": True})[0], 200)
        self.assertIsNone(self.dr.match)
        self.assertFalse((self.live / "draft-match.json").exists())

    def test_the_hosts_own_script_is_allowed(self):
        self.assertEqual(self.req("GET", "/api/ready/state")[0], 200)
        self.assertEqual(self.publish()[0], 200)
        self.assertEqual(self.req("GET", "/api/ready/state", host="localhost:%d" % self.port)[0], 200)

    def test_publish_needs_json(self):
        code, _, _ = self.req("POST", "/api/match/publish", json.dumps({"links": {"Michael": MICHAEL_LINK}}).encode(), ctype="text/plain")
        self.assertEqual(code, 415)                          # the one content type a cross-site form/fetch cannot send without a preflight
        self.assertEqual(self.req("POST", "/api/match/publish", b"{nope")[0], 400)
        self.assertEqual(self.req("POST", "/api/match/publish", [1, 2])[0], 400)

    def test_tunnelled_players_can_still_use_their_own_routes(self):
        h = {"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9"}
        host = "quiet-river-1234.trycloudflare.com"
        self.assertEqual(self.req("GET", "/api/state", headers=h, host=host, token="Sam")[0], 200)
        self.assertEqual(self.req("POST", "/api/ready", {"ready": True}, headers=h, host=host, token="Sam")[0], 200)
        self.assertEqual(self.req("GET", "/api/match", headers=h, host=host, token="Sam")[0], 200)

    def test_ready_state_has_no_secrets_and_no_links(self):
        self.publish()
        raw = self.req("GET", "/api/ready/state")[2]
        for s in SECRETS + tuple(self.tok.values()):
            self.assertNotIn(s, raw)
        self.assertNotIn("http", raw)


class LinkTests(Base):
    def test_good_links_are_accepted_and_the_reply_carries_no_link(self):
        code, d, raw = self.publish()
        self.assertEqual((code, d), (200, {"ok": True, "published": ["Michael", "Sam"]}))
        for s in SECRETS:
            self.assertNotIn(s, raw)

    def test_bad_links_are_refused(self):
        bad = ["javascript:alert(1)", "data:text/html,hi", "ftp://127.0.0.1/x", "http://evil.example.com/me#key=1",
               "https://evil.example.com/me#key=1", "http://quiet-river-1234.trycloudflare.com/me",          # tunnel must be https
               "https://trycloudflare.com.evil.example.com/me", "https://evil.com/.trycloudflare.com",
               "https://x.trycloudflare.com@evil.example.com/me", "http://127.0.0.1@evil.example.com/me",
               "https://user:pw@x.trycloudflare.com/me", "http://127.0.0.1:99999/me", "https://x.trycloudflare.com/a b",
               "https://x.trycloudflare.com/a\nb", "https://x.trycloudflare.com\\@evil.com/", "//x.trycloudflare.com/me", "",
               "https://x.trycloudflare.com/" + "a" * 2100, 5, None, ["https://x.trycloudflare.com/"]]
        for url in bad:
            code, d, raw = self.publish({"Michael": url})
            self.assertEqual(code, 400, repr(url)[:60])
            self.assertIsNone(self.dr.match)

    def test_unknown_player_or_missing_links(self):
        self.assertEqual(self.publish({"Mallory": MICHAEL_LINK})[0], 400)
        self.assertEqual(self.req("POST", "/api/match/publish", {"note": "x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/match/publish", {"links": {}})[0], 400)
        self.assertEqual(self.req("POST", "/api/match/publish", {"links": ["a"]})[0], 400)

    def test_a_player_name_matches_regardless_of_case(self):
        self.assertEqual(self.publish({"michael": MICHAEL_LINK})[1]["published"], ["Michael"])

    def test_publish_before_the_draft_ends_is_refused(self):
        self.start(finish=False)
        self.assertEqual(self.publish()[0], 409)

    def test_a_second_publish_replaces_the_first(self):
        self.publish()
        self.publish({"Michael": MICHAEL_LINK})
        self.assertIsNone(self.req("GET", "/api/match", token="Sam")[1]["link"])

    def test_note_is_trimmed_and_controls_stripped(self):
        self.req("POST", "/api/match/publish", {"links": {"Sam": SAM_LINK}, "note": "a\x00b\x1b[31m" + "x" * 500})
        note = self.req("GET", "/api/match", token="Sam")[1]["note"]
        self.assertNotIn("\x00", note); self.assertNotIn("\x1b", note); self.assertEqual(len(note), 200)

    def test_an_extra_tunnel_suffix_from_the_environment(self):
        with mock.patch.object(S, "LINK_SUFFIXES", S.LINK_SUFFIXES + (".example-tunnel.net",)):
            self.assertEqual(self.publish({"Sam": "https://abc.example-tunnel.net/me#key=1"})[0], 200)


class OwnLinkOnlyTests(Base):
    def test_each_player_gets_only_their_own_link(self):
        self.publish()
        code, m, raw = self.req("GET", "/api/match", token="Michael")
        self.assertEqual((m["status"], m["link"], m["note"]), ("ready", MICHAEL_LINK, "table 1"))
        for s in ("SAM-SECRET-KEY-BBBB", "quiet-river-1234", "trycloudflare"):
            self.assertNotIn(s, raw)
        code, m, raw = self.req("GET", "/api/match", token="Sam")
        self.assertEqual(m["link"], SAM_LINK)
        for s in ("MICHAEL-SECRET-KEY-AAAA", "127.0.0.1:18800"):
            self.assertNotIn(s, raw)

    def test_an_agent_or_a_player_with_no_link_gets_none(self):
        self.publish({"Michael": MICHAEL_LINK})
        for who in ("Sam", "Claude", "Fusion"):
            m = self.req("GET", "/api/match", token=who)[1]
            self.assertIsNone(m["link"], who)
        self.assertEqual(self.req("GET", "/api/match")[0], 403)                               # no token
        self.assertEqual(self.req("GET", "/api/match?k=wrong")[0], 403)

    def test_link_appears_nowhere_else(self):
        self.publish()
        everything = []
        for who in S.NAMES.values():
            everything.append(self.req("GET", "/api/state", token=who)[2])
            everything.append(self.req("GET", "/api/banter?since=0", token=who)[2])
        everything.append(self.req("GET", "/api/ready/state")[2])
        everything.append(self.req("GET", "/")[2])
        banter = self.live / "draft-banter.jsonl"
        everything.append(banter.read_text() if banter.exists() else "")
        for text in everything:
            for s in SECRETS:
                self.assertNotIn(s, text)
        for s in (self.dr.match["links"]["Michael"], self.dr.match["links"]["Sam"]):
            self.assertFalse(any(s in l.get("text", "") for l in self.dr.banter.lines))

    def test_nothing_is_printed_or_logged(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.publish()
            self.req("GET", "/api/match", token="Michael")
            self.req("GET", "/api/ready/state", headers={"CF-Connecting-IP": "203.0.113.9"})
            self.req("POST", "/api/match/publish", {"links": {"Sam": "https://evil.example.com/#key=SAM-SECRET-KEY-BBBB"}})
        for text in (out.getvalue(), err.getvalue()):
            for s in SECRETS + tuple(self.tok.values()):
                self.assertNotIn(s, text)

    def test_the_file_is_private(self):
        self.publish()
        f = self.live / "draft-match.json"
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        data = json.loads(f.read_text())
        self.assertEqual(data["links"], {"Michael": MICHAEL_LINK, "Sam": SAM_LINK})
        self.assertEqual(list(self.live.glob("*.tmp")), [])
        # replacing it keeps it private even if something left a lax temp file behind
        (self.live / "draft-match.json.tmp").write_text("x"); os.chmod(self.live / "draft-match.json.tmp", 0o644)
        self.publish({"Sam": SAM_LINK})
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)

    def test_responses_are_not_cacheable(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.request("GET", "/api/match?k=" + self.tok["Michael"], headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse(); r.read()
        self.assertEqual(r.getheader("Cache-Control"), "no-store")


class StatusTests(Base):
    def status(self, who="Michael"):
        return self.req("GET", "/api/match", token=who)[1]["status"]

    def test_waiting_then_starting_then_ready(self):
        self.assertEqual(self.status(), "waiting")
        for who in ("Michael", "Sam", "Claude"):
            self.ready(who)
        self.assertEqual(self.status(), "waiting")
        self.ready("Fusion")
        self.assertEqual((self.status(), self.status("Sam")), ("starting", "starting"))
        self.assertTrue(self.req("GET", "/api/ready/state")[1]["all_ready"])
        self.publish()
        self.assertEqual((self.status(), self.status("Sam")), ("ready", "ready"))
        self.assertEqual(self.status("Claude"), "starting")                    # an agent has no link, so never "ready"
        self.req("POST", "/api/match/publish", {"clear": True})
        self.assertEqual(self.status(), "starting")

    def test_the_draft_not_being_over_is_waiting(self):
        self.start(finish=False)
        self.assertEqual(self.status(), "waiting")
        st = self.req("GET", "/api/ready/state")[1]
        self.assertFalse(st["draft_done"]); self.assertFalse(st["all_ready"])

    def test_the_published_match_survives_a_restart_but_not_a_new_draft(self):
        self.publish()
        self.start(keep=True)
        self.assertEqual(self.req("GET", "/api/match", token="Sam")[1]["link"], SAM_LINK)
        self.assertEqual(self.req("GET", "/api/match", token="Michael")[1]["link"], MICHAEL_LINK)
        self.start(finish=True, seed=999)                                       # a different draft: the old file is ignored
        self.assertIsNone(self.req("GET", "/api/match", token="Sam")[1]["link"])

    def test_an_unusable_match_file_is_ignored(self):
        (self.live / "draft-match.json").write_text("{not json")
        self.start(keep=True)
        self.assertEqual(self.status(), "waiting")
        (self.live / "draft-match.json").write_text(json.dumps({"seed": self.SEED, "links": {"Michael": "https://evil.example.com/"}}))
        self.start(keep=True)
        self.assertEqual(self.status(), "waiting")


class PageTests(unittest.TestCase):
    def test_the_page_never_interpolates_a_link_into_html(self):
        page = S.PAGE
        self.assertNotIn("${MATCH.link", page)
        self.assertNotIn("href=${", page)
        self.assertIn("esc(MATCH.note", page)                       # the host's note is escaped
        self.assertIn("esc(p.name)", page)                           # so are the names in the strip
        self.assertIn("/^https?:\\/\\//i.test(l)", page)            # and only http(s) is ever opened
        self.assertIn("/api/match?k=", page)

    def test_no_link_or_key_is_in_the_served_page(self):
        self.assertNotIn("key=", S.PAGE.split("// ---- \"I'm ready\"")[1].split("// ---- sound effects")[0])


if __name__ == "__main__":
    unittest.main()
