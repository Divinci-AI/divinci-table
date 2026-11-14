"""The round launcher starts a ROUND, leaves matches alone, and hands a seat to the autopilot in the open (retro M4, M5, M3; 2026-11-14).

    python3 launch/test_play_round.py        (real tables on free ports, a scratch live folder, a stand-in tunnel and edge: no network)

On 2026-11-13 the launcher wanted exactly two pairings and restarted both tables for every game: a match that was already decided was
played on (Claude and Fusion played a game 3 nobody needed), the remote player got a new address every time, and an absent player's
seat was put on the autopilot by hand with nothing at the table saying so. Proved here with launch/play_round.sh itself:

  1. two pairings -> two tables, each a best-two-out-of-three match; keys in 0600 files and never on the screen; the autopilot's seats
     are declared to the table; the remote player's link is written only after the tunnel is proved (the edge saw the proof request);
  2. the same command again: both matches are still being played, so NOTHING is restarted (same table processes, same tunnel, same link);
  3. one match is finished: it is NOT replayed (the retro's bug), and the other is still left alone;
  4. --restart PORT restarts exactly that table; a different pairing aimed at a table still in use is REFUSED and nothing is touched;
  5. a finished table is saved and replaced by a new pairing; ONE pairing is enough; `Name=bot` seats are played by the autopilot,
     the table says "standing in" in its log, and the old table's helper processes are gone;
  6. mid-match (table_tools.py stand-in): a person's seat goes to the autopilot and the table says so; the person's own move takes it
     back and the bot stands by; `on` again and it plays on; `off` stops it;
  7. round-wait reports each game and the match, writes the result, and exits 0 when the round is done;
  8. the tunnel across a table restart: a tunnel that is up is KEPT (same address) and the link is proved again and carries the NEW key;
     a dead tunnel is replaced; an address that does not reach the table gives NO link, exit 4, and says so.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path[:0] = [str(HERE), str(REPO / "table"), str(REPO / "table" / "tests")]
sys.dont_write_bytecode = True
import cards_path  # noqa: E402
import fake_edge  # noqa: E402
import tunnels  # noqa: E402
import match_bo3_test_lib as L  # noqa: E402  (skips, exit 77, when the set's card data is not here)

R, check = L.R, L.check
PY = sys.executable
TMP = Path(tempfile.mkdtemp(prefix="round-"))
LIVE, DECKS, EDGE_DIR = TMP / "live", TMP / "decks", TMP / "edge"
for d in (LIVE, DECKS, EDGE_DIR):
    d.mkdir()
os.chmod(LIVE, 0o700)
SEEN: list = []
EDGE = fake_edge.serve(EDGE_DIR, seen=SEEN)
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for name in ("michael", "sam", "claude", "fusion", "ann", "ben"):
    (DECKS / f"{name}-g5.json").write_text(json.dumps({"name": name, "commander": None, "startingLife": 20, "mainBoard": [
        R.shim.entry(dict(L.RAW["Mountain"]), 17), R.shim.entry(dict(L.RAW["Starfleet Security"]), 23)]}))
P0, P1 = R.free_port(), R.free_port()
ENV = {**os.environ, "LIVE_DIR": str(LIVE), "DECK_DIR": str(DECKS), "TABLE_PY": PY, "DRYRUN": "1", "NO_CLIPBOARD": "1", "NO_NOTIFY": "1",
       "REMOTE_SEAT": "Sam", "BOT_SEATS": "Claude,Fusion", "CLAUDE_BANTER": "0", "CLAUDE_AUTOPASS": "0", "TUNNEL_WATCH": "0",
       "TUNNEL_CMD": f"{PY} {HERE / 'fake_tunnel.py'} --port {{port}} --edge-dir {EDGE_DIR} --linger 0.5",
       "TUNNEL_PROBE_VIA": f"http://127.0.0.1:{EDGE.server_address[1]}", "TUNNEL_PROVE_SECS": "6",
       "TABLE_HIGHROLL": "first", "TABLE_NO_VOICE": "1", "TABLE_MATCH_COUNTDOWN": "2", "TABLE_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1",
       "AUTOPILOT_GRACE": "0", "BOT_PY": PY, "NO_GEMMA": "1", "HF_HUB_OFFLINE": "1"}
ENV.pop("TABLE_MATCH", None)


def launch(*args, **extra):
    r = subprocess.run(["bash", str(HERE / "play_round.sh"), *map(str, args)], capture_output=True, text=True, env={**ENV, **extra}, timeout=240, cwd=TMP)
    return r.returncode, r.stdout + r.stderr


def tool(*args, **extra):
    r = subprocess.run([PY, str(HERE / "table_tools.py"), *map(str, args)], capture_output=True, text=True, env={**ENV, **extra}, timeout=120)
    return r.returncode, r.stdout + r.stderr


def api(port, method, path, body=None, token=None, key=None, driver=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-Brain-Token"] = token
    if key:
        h["X-Seat-Key"] = key
    if driver:
        h["X-Seat-Driver"] = driver
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with LOCAL.open(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except (urllib.error.URLError, OSError):
        return 0, {}


def until(pred, secs=40):
    end = time.time() + secs
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001 — not there yet
            pass
        time.sleep(0.2)
    return False


def pids(port):
    try:
        return {r["name"]: int(r["pid"]) for r in json.loads((LIVE / "pids" / f"{port}.json").read_text())}
    except (OSError, ValueError):
        return {}


def played(port, seat):
    return next((s for s in api(port, "GET", "/api/played-by")[1].get("seats", []) if s["name"] == seat), {})


def key(seat):
    return (LIVE / f"{seat.lower()}-seat.key").read_text().strip()


def finish_match(port, token_file, winner, loser):
    """The host ends two games in a row for `winner` (a life total taken to 0, as the last point of damage would): the match is over."""
    tok = (LIVE / token_file).read_text().strip()
    for n in (1, 2):
        assert until(lambda: (api(port, "GET", "/api/match")[1].get("current") or {}).get("n") == n and api(port, "GET", "/api/phase")[1].get("player")), f"game {n} never began on :{port}"
        api(port, "POST", "/api/brain/life", {"seat": winner, "player": loser, "delta": -60, "text": "The last blow."}, token=tok)
        assert until(lambda: len(api(port, "GET", "/api/match")[1].get("games") or []) == n), f"game {n} never ended on :{port}"
    assert until(lambda: api(port, "GET", "/api/match")[1].get("over")), "the match never ended"


ALL_PIDS: set = set()


def remember():
    for f in (LIVE / "pids").glob("*.json") if (LIVE / "pids").is_dir() else []:
        try:
            ALL_PIDS.update(int(r["pid"]) for r in json.loads(f.read_text()))
        except (OSError, ValueError):
            pass
    for f in (LIVE / "tunnels").glob("*.json") if (LIVE / "tunnels").is_dir() else []:
        try:
            ALL_PIDS.add(int(json.loads(f.read_text())["pid"]))
        except (OSError, ValueError, KeyError):
            pass


try:
    print("1. Two pairings: two tables, two matches", flush=True)
    c, out = launch("--ports", f"{P0},{P1}", "Michael:Sam", "Claude:Fusion")
    remember()
    check(c == 0 and f"table :{P0}  Michael vs Sam: starting" in out and f"table :{P1}  Claude vs Fusion: starting" in out, "the launcher starts both tables", (c, out[-900:]))
    check((LIVE / f"token-{P0}").stat().st_mode & 0o777 == 0o600 and (LIVE / f"token-{P1}").exists() and (LIVE / f"server-{P1}.log").exists(),
          "each table's own files are named after its port (so a later launch of one table never takes another table's token)")
    m0, m1 = api(P0, "GET", "/api/match")[1], api(P1, "GET", "/api/match")[1]
    check(m0.get("on") and m0.get("seats") == ["Michael", "Sam"] and m1.get("on") and m1.get("seats") == ["Claude", "Fusion"], "each table plays a best-two-out-of-three match", (m0, m1))
    keys = {n: key(n) for n in ("Michael", "Sam", "Claude", "Fusion")}
    check(all((LIVE / f"{n.lower()}-seat.key").stat().st_mode & 0o777 == 0o600 for n in keys) and not any(k in out for k in keys.values()),
          "every seat key is in its own 0600 file and none is on the screen")
    check(played(P1, "Claude").get("kind") == "autopilot" and not played(P1, "Claude").get("stand_in") and played(P1, "Fusion").get("kind") == "autopilot",
          "the table is told that the rules autopilot plays Claude's and Fusion's seats (their own seats: not a stand-in)", api(P1, "GET", "/api/played-by")[1])
    check(played(P0, "Michael").get("kind") == "unknown" and played(P0, "Sam").get("kind") == "unknown", "a person's seat is not called a person's before anybody has played it")
    link = (LIVE / "sam-link.txt").read_text().strip()
    rec0 = tunnels.read_record(LIVE, P0)
    host0 = tunnels.host_of(Path(rec0["log"]))
    check(link.startswith(host0 + "/me?player=Sam#key=") and link.endswith(keys["Sam"]) and ("GET", host0.split("//")[1], "/api/instance") in SEEN,
          "Sam's link was written after the tunnel was proved to reach HIS table (the edge saw the proof request)", (out[-400:], SEEN[-2:]))
    check(tunnels.read_record(LIVE, P1) is None, "no tunnel is opened to the table the remote player is not at")
    rd = json.loads((LIVE / "round.json").read_text())
    check([t["port"] for t in rd["tables"]] == [P0, P1] and rd["tables"][0]["remote"] == "Sam" and rd["tables"][1]["bots"] == ["Claude", "Fusion"] and rd["match"] == "bo3",
          "live/round.json says what the round is", rd)
    seats_page = (LIVE / "seats.html").read_text()
    check((LIVE / "seats.html").stat().st_mode & 0o777 == 0o600 and keys["Sam"] not in seats_page and "Michael" in seats_page, "the local seats page (0600) has no key of the remote player")
    check(until(lambda: api(P1, "GET", "/api/phase")[1].get("player") and api(P0, "GET", "/api/phase")[1].get("player") is not None, 30), "both games are under way")

    print("2. The same command again: nothing is restarted", flush=True)
    before = (pids(P0).get("table"), pids(P1).get("table"), rec0["pid"], (LIVE / "sam-link.txt").stat().st_mtime_ns)
    c, out = launch("--ports", f"{P0},{P1}", "Michael:Sam", "Claude:Fusion")
    remember()
    check(c == 0 and out.count("LEFT ALONE") == 2 and "starting" not in out, "both tables are LEFT ALONE: their matches are still being played", (c, out))
    check((pids(P0).get("table"), pids(P1).get("table"), tunnels.read_record(LIVE, P0)["pid"], (LIVE / "sam-link.txt").stat().st_mtime_ns) == before
          and all(tunnels.pid_alive(p) for p in before[:3]) and key("Sam") == keys["Sam"], "the same table processes, the same tunnel, the same link, the same keys")

    print("3. A finished match is not replayed", flush=True)
    finish_match(P1, f"token-{P1}", "Claude", "Fusion")
    c, out = launch("--ports", f"{P0},{P1}", "Michael:Sam", "Claude:Fusion")
    remember()
    check(c == 0 and f"table :{P1}  Claude vs Fusion: NOT REPLAYED" in out and "Claude wins the match 2" in out and f"table :{P0}  Michael vs Sam: LEFT ALONE" in out,
          "the finished match is NOT REPLAYED (the launcher says the score), the other is left alone", (c, out))
    m1 = api(P1, "GET", "/api/match")[1]
    check(pids(P1).get("table") == before[1] and m1.get("over") and len(m1.get("games") or []) == 2, "the same table process, still showing the final score: no game 3", m1.get("score"))

    print("4. --restart PORT, and a pairing aimed at a table in use", flush=True)
    c, out = launch("--ports", f"{P0},{P1}", "--restart", P1, "Michael:Sam", "Claude:Fusion")
    remember()
    check(c == 0 and f"table :{P1}  Claude vs Fusion: starting (--restart {P1})" in out and f"table :{P0}  Michael vs Sam: LEFT ALONE" in out, "--restart restarts exactly that table", (c, out))
    check(pids(P1).get("table") != before[1] and not tunnels.pid_alive(before[1]) and pids(P0).get("table") == before[0] and not api(P1, "GET", "/api/match")[1].get("over"),
          "a new table process on that port with a new match; the other table untouched")
    saved = sorted(p.name for p in (LIVE / "games").iterdir())
    check(len(saved) == 1 and saved[0].endswith(f"-{P1}-Claude-Fusion") and json.loads((LIVE / "games" / saved[0] / "match.json").read_text()).get("over"),
          "the finished match was saved before its table was stopped (with the match's own record)", saved)
    t1 = pids(P1).get("table")
    c, out = launch("--ports", P1, "Ann=bot:Ben=bot")
    check(c == 1 and "REFUSED" in out and "nothing was started or stopped" in out and pids(P1).get("table") == t1 and tunnels.pid_alive(t1),
          "another pairing aimed at a table whose match is still being played is REFUSED, and nothing is touched", (c, out))

    print("5. A finished table is replaced; one pairing is enough; Name=bot", flush=True)
    old_bots = [p for n, p in pids(P1).items() if n.startswith("autopilot-")]
    finish_match(P1, f"token-{P1}", "Fusion", "Claude")
    c, out = launch("--ports", P1, "Ann=bot:Ben=bot")
    remember()
    check(c == 0 and f"table :{P1}  Ann vs Ben: starting (the finished table of Claude and Fusion is replaced)" in out, "one pairing: the finished table is replaced", (c, out))
    check(api(P1, "GET", "/api/match")[1].get("seats") == ["Ann", "Ben"] and pids(P0).get("table") == before[0], "the new pairing plays there; the other table is not in this launch and is untouched")
    check(len(old_bots) == 2 and until(lambda: not any(tunnels.pid_alive(p) for p in old_bots), 10), "the old table's autopilots were stopped (each by its own pid)", old_bots)
    pa = played(P1, "Ann")
    check(pa.get("kind") == "autopilot" and pa.get("stand_in") is True and played(P1, "Ben").get("stand_in") is True, "Ann=bot, Ben=bot: the table shows the autopilot STANDING IN at both seats", pa)
    ev = api(P1, "GET", "/api/events?since=0")[1].get("events", [])
    check(any(e.get("type") == "table-note" and e.get("text") == "Ann's seat is now played by the rules autopilot. A move from Ann's own page takes it back." for e in ev),
          "…and the public log says so")
    check(len(sorted((LIVE / "games").iterdir())) == 2, "that finished match was saved too")
    rd = json.loads((LIVE / "round.json").read_text())
    check(len(rd["tables"]) == 1 and rd["tables"][0]["bots"] == ["Ann", "Ben"], "live/round.json is the new round", rd)

    print("6. Mid-match: a person's seat goes to the autopilot and back", flush=True)
    c, out = launch("--ports", f"{P0},{P1}", "Michael:Sam", "Ann=bot:Ben=bot")      # (the round as it stands: both left alone; round.json names both tables again)
    check(c == 0 and out.count("LEFT ALONE") == 2, "(the round as it stands: both tables left alone)", (c, out))
    c, out = tool("stand-in", "--live", LIVE, "--seat", "sam", "on")
    remember()
    sp = pids(P0).get("autopilot-Sam")
    check(c == 0 and sp and tunnels.pid_alive(sp) and "handed to the rules autopilot" in out and key("Sam") not in out, "stand-in on: an autopilot process for Sam's seat", (c, out))
    ps = played(P0, "Sam")
    check(ps.get("kind") == "autopilot" and ps.get("stand_in") is True, "the table shows Sam's seat as played by the rules autopilot, standing in", ps)
    botlog = LIVE / "sam-pilot.log"
    check(until(lambda: "the table shows Sam's seat as played by the rules autopilot" in botlog.read_text(), 15), "the bot is at the seat", botlog.read_text()[-300:])
    ph = api(P0, "GET", "/api/phase")[1]
    api(P0, "POST", "/api/brain/pass", {"seat": "Sam", "player": ph.get("player"), "step": ph.get("step")}, key=key("Sam"))     # Sam is back and makes a move
    check(played(P0, "Sam").get("kind") == "person", "Sam makes a move from his own page: the seat is his again")
    ev = api(P0, "GET", "/api/events?since=0")[1].get("events", [])
    check(any(e.get("text") == "Sam is back: Sam's seat is played by a person again." for e in ev), "…and the log says: Sam is back")
    check(until(lambda: "standing by" in botlog.read_text(), 20) and tunnels.pid_alive(sp), "the bot stands by (it is still running, and plays nothing)", botlog.read_text()[-300:])
    c, out = tool("stand-in", "--live", LIVE, "--seat", "Sam", "on")
    check(c == 0 and "plays on" in out and pids(P0).get("autopilot-Sam") == sp and until(lambda: "playing on" in botlog.read_text(), 20),
          "stand-in on again: the same process plays on (no second bot)", (c, out, botlog.read_text()[-200:]))
    c, out = tool("stand-in", "--live", LIVE, "--seat", "Sam", "off")
    check(c == 0 and until(lambda: not tunnels.pid_alive(sp), 10) and played(P0, "Sam").get("kind") == "person", "stand-in off: the autopilot is stopped and the seat is Sam's", (c, out))
    c, out = tool("stand-in", "--live", LIVE, "--seat", "Nobody", "on")
    check(c == 1 and "at no table of this round" in out, "a seat that is at no table of the round is refused", (c, out))

    print("7. round-wait: each game, the match, the round", flush=True)
    c, out = launch("--ports", P1, "--restart", P1, "Ann=bot:Ben=bot")              # a fresh match, so the two games ended below are games 1 and 2
    remember()
    w = subprocess.Popen([PY, "-u", str(HERE / "table_tools.py"), "round-wait", "--live", str(LIVE), "--ports", str(P1), "--poll", "0.5", "--max-secs", "120"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=ENV)
    finish_match(P1, f"token-{P1}", "Ann", "Ben")
    wout, _ = w.communicate(timeout=60)
    check(w.returncode == 0 and "game 1: Ann wins" in wout and "game 2: Ann wins" in wout and "MATCH OVER: Ann wins the match 2" in wout and "ROUND DONE: Ann vs Ben: Ann wins the match 2" in wout,
          "it reports each game, the match, and ROUND DONE, and exits 0", (w.returncode, wout))
    check(played(P1, "Ann").get("kind") == "autopilot" and played(P1, "Ann").get("stand_in") is True and api(P1, "GET", "/api/match")[1]["played_by"]["Ben"]["kind"] == "autopilot",
          "who plays each seat is kept from game to game of the match (and /api/match carries it)", played(P1, "Ann"))
    res = json.loads((LIVE / "round-result.json").read_text())["results"][0]
    check(res["over"] and res["winner"] == "Ann" and res["wins"] == {"Ann": 2, "Ben": 0}, "live/round-result.json has the result", res)

    print("8. The tunnel across a table restart", flush=True)
    link_before, key_before = (LIVE / "sam-link.txt").read_text().strip(), key("Sam")
    seen_n = len(SEEN)
    c, out = launch("--ports", P0, "--restart", P0, "Michael:Sam")
    remember()
    rec_a = tunnels.read_record(LIVE, P0)
    link_a = (LIVE / "sam-link.txt").read_text().strip()
    check(c == 0 and rec_a["pid"] == rec0["pid"] and "keeping the tunnel" in out, "the table is restarted; the tunnel that is still up is KEPT", (c, out[-600:]))
    check(key("Sam") != key_before and link_a.startswith(host0 + "/") and link_a.endswith(key("Sam")) and link_a != link_before,
          "the same address, the NEW key: the link was rewritten")
    check(("GET", host0.split("//")[1], "/api/instance") in SEEN[seen_n:], "…after the address was proved again, against the new table")
    os.kill(rec_a["pid"], signal.SIGKILL)              # the tunnel dies (this test started it)
    until(lambda: not tunnels.pid_alive(rec_a["pid"]))
    c, out = launch("--ports", P0, "--restart", P0, "Michael:Sam")
    remember()
    rec_b = tunnels.read_record(LIVE, P0)
    host_b = tunnels.host_of(Path(rec_b["log"]))
    link_b = (LIVE / "sam-link.txt").read_text().strip()
    check(c == 0 and rec_b["pid"] != rec_a["pid"] and host_b != host0 and link_b.startswith(host_b + "/") and host0 not in link_b,
          "a dead tunnel is replaced: the link names the new address and never the dead one", (c, out[-500:]))
    c, out = launch("--ports", P0, "--restart", P0, "Michael:Sam", TUNNEL_PROVE_SECS="2",
                    TUNNEL_CMD=f"{PY} {HERE / 'fake_tunnel.py'} --port {{port}} --edge-dir {EDGE_DIR} --no-register --linger 0.2")
    remember()
    os.kill(tunnels.read_record(LIVE, P0)["pid"], signal.SIGTERM)       # (so the next launch cannot keep it)
    c, out = launch("--ports", P0, "--restart", P0, "Michael:Sam", TUNNEL_PROVE_SECS="2",
                    TUNNEL_CMD=f"{PY} {HERE / 'fake_tunnel.py'} --port {{port}} --edge-dir {EDGE_DIR} --no-register --linger 0.2")
    remember()
    check(c == 4 and "NO LINK for Sam" in out and "Sam HAS NO LINK" in out and not (LIVE / "sam-link.txt").exists(),
          "an address that does not reach the table: NO link file, exit 4, and the launcher says what to do", (c, out[-700:]))
    check(api(P0, "GET", "/api/match")[1].get("on") is True, "…the table itself is up")
    check(not any(k in out for k in (key("Sam"), key("Michael"))), "no key on the screen in any of it")
finally:
    remember()

    def ours(pid):                                     # a pid this test's launcher wrote down AND that is still one of its processes
        cmd = tunnels.pid_command(pid) if tunnels.pid_alive(pid) else ""
        return bool(cmd) and (str(TMP) in cmd or str(REPO / "bots") in cmd or str(HERE) in cmd)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in ALL_PIDS:
            try:
                if ours(pid):
                    os.kill(pid, sig)
            except OSError:
                pass
        time.sleep(1.0)
    EDGE.shutdown()

print("\nFAILED: " + "; ".join(R.FAILS) if R.FAILS else "\nall good")
sys.exit(1 if R.FAILS else 0)
