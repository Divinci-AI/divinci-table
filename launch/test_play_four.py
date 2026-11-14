"""The four-player launcher, and a seat's deck named outright (retro M7, 2026-11-14).

    python3 launch/test_play_four.py     (a real table on a free port, a scratch live folder, a stand-in tunnel and edge: no network)

  1. launch/play_four.sh: the four decks are built from the draft's deck lists; one table with four seats and NO match (a four-player
     table is a single game); the autopilot's seats are declared to the table; the remote player's link is written only after the
     tunnel is proved; keys in 0600 files and none on the screen;
  2. a second launch while the game is still running is REFUSED and touches nothing; --force replaces it (the old table and its
     helpers are stopped by pid, the finished game is saved first);
  3. a deck list that is too short stops the launch before anything is started;
  4. launch/play_round.sh --deck Name=FILE: a seat plays the deck file it is given, whatever is in the deck folder.
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
import cards_path  # noqa: E402, F401
import fake_edge  # noqa: E402
import tunnels  # noqa: E402
import match_bo3_test_lib as L  # noqa: E402  (skips, exit 77, when the set's card data is not here)

R, check = L.R, L.check
PY = sys.executable
TMP = Path(tempfile.mkdtemp(prefix="four-"))
LIVE, EDGE_DIR = TMP / "live", TMP / "edge"
LIVE.mkdir(); EDGE_DIR.mkdir()
os.chmod(LIVE, 0o700)
SEEN: list = []
EDGE = fake_edge.serve(EDGE_DIR, seen=SEEN)
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))
NAMES = ("Michael", "Sam", "Claude", "Fusion")
for n in NAMES:
    (LIVE / f"deck-{n.lower()}.txt").write_text("17 Mountain\n23 Starfleet Security\n")
PORT, PORT2 = R.free_port(), R.free_port()
ENV = {**os.environ, "LIVE_DIR": str(LIVE), "TABLE_PY": PY, "DRYRUN": "1", "NO_CLIPBOARD": "1", "NO_NOTIFY": "1", "REMOTE_SEAT": "Sam", "BOT_SEATS": "Claude,Fusion",
       "CLAUDE_BANTER": "0", "CLAUDE_AUTOPASS": "0", "TUNNEL_WATCH": "0", "DRAFT_CARDS_JSON": cards_path.cards_json(),
       "TUNNEL_CMD": f"{PY} {HERE / 'fake_tunnel.py'} --port {{port}} --edge-dir {EDGE_DIR} --linger 0.3",
       "TUNNEL_PROBE_VIA": f"http://127.0.0.1:{EDGE.server_address[1]}", "TUNNEL_PROVE_SECS": "6",
       "TABLE_HIGHROLL": "first", "TABLE_NO_VOICE": "1", "TABLE_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1", "AUTOPILOT_GRACE": "0", "BOT_PY": PY,
       "NO_GEMMA": "1", "HF_HUB_OFFLINE": "1"}
ENV.pop("TABLE_MATCH", None)
ALL_PIDS: set = set()


def run(script, *args, **extra):
    r = subprocess.run(["bash", str(HERE / script), *map(str, args)], capture_output=True, text=True, env={**ENV, **extra}, timeout=240, cwd=TMP)
    for f in (LIVE / "pids").glob("*.json") if (LIVE / "pids").is_dir() else []:
        try:
            ALL_PIDS.update(int(x["pid"]) for x in json.loads(f.read_text()))
        except (OSError, ValueError):
            pass
    for f in (LIVE / "tunnels").glob("*.json") if (LIVE / "tunnels").is_dir() else []:
        try:
            ALL_PIDS.add(int(json.loads(f.read_text())["pid"]))
        except (OSError, ValueError, KeyError):
            pass
    return r.returncode, r.stdout + r.stderr


def api(port, path):
    try:
        with LOCAL.open(f"http://127.0.0.1:{port}{path}", timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (urllib.error.URLError, OSError):
        return 0, {}


def pids(port):
    try:
        return {r["name"]: int(r["pid"]) for r in json.loads((LIVE / "pids" / f"{port}.json").read_text())}
    except (OSError, ValueError):
        return {}


def key(seat):
    return (LIVE / f"{seat.lower()}-seat.key").read_text().strip()


try:
    print("1. One four-player table", flush=True)
    c, out = run("play_four.sh", "--port", PORT)
    check(c == 0 and f"game started on http://127.0.0.1:{PORT}" in out, "the launcher starts the table and the game", (c, out[-900:]))
    check(all(json.loads((LIVE / "decks" / f"{n.lower()}-g5.json").read_text())["mainBoard"] for n in NAMES), "the four decks were built from the draft's deck lists")
    life = api(PORT, "/api/life")[1]
    check(sorted(life) == sorted(NAMES) and api(PORT, "/api/match")[1] == {"on": False}, "four seats at one table, and no match (a four-player table is a single game)", life)
    pb = {s["name"]: s for s in api(PORT, "/api/played-by")[1].get("seats", [])}
    check(pb["Claude"]["kind"] == "autopilot" and pb["Fusion"]["kind"] == "autopilot" and not pb["Claude"].get("stand_in") and pb["Michael"]["kind"] == "unknown",
          "the table is told the autopilot plays Claude's and Fusion's seats; a person's seat is not guessed", pb)
    keys = {n: key(n) for n in NAMES}
    rec = tunnels.read_record(LIVE, PORT)
    host = tunnels.host_of(Path(rec["log"]))
    link = (LIVE / "sam-link.txt").read_text().strip()
    check(link.startswith(host + "/me?player=Sam#key=") and link.endswith(keys["Sam"]) and ("GET", host.split("//")[1], "/api/instance") in SEEN,
          "Sam's link was written after the tunnel was proved to reach the table")
    check(not any(k in out for k in keys.values()) and all((LIVE / f"{n.lower()}-seat.key").stat().st_mode & 0o777 == 0o600 for n in NAMES),
          "keys in 0600 files, none on the screen")
    seats_page = (LIVE / "seats.html").read_text()
    check("Michael" in seats_page and "Claude (autopilot)" in seats_page and keys["Sam"] not in seats_page, "the local seats page lists the local seats, not the remote player's key")

    print("2. A game still running is not replaced unless asked", flush=True)
    t0 = pids(PORT).get("table")
    c, out = run("play_four.sh", "--port", PORT)
    check(c == 1 and "REFUSED: a game is still running" in out and pids(PORT).get("table") == t0 and tunnels.pid_alive(t0) and key("Sam") == keys["Sam"],
          "a second launch is REFUSED: the same table, the same keys", (c, out))
    old_bots = [p for n, p in pids(PORT).items() if n.startswith("autopilot-")]
    c, out = run("play_four.sh", "--port", PORT, "--force")
    check(c == 0 and pids(PORT).get("table") != t0 and not tunnels.pid_alive(t0) and key("Sam") != keys["Sam"], "--force replaces it: a new table process, new keys", (c, out[-600:]))
    gone = True
    end = time.time() + 10
    while time.time() < end and any(tunnels.pid_alive(p) for p in old_bots):
        time.sleep(0.2)
    check(len(old_bots) == 2 and not any(tunnels.pid_alive(p) for p in old_bots), "the old table's autopilots were stopped, each by its own pid", old_bots)
    saved = sorted(p.name for p in (LIVE / "games").iterdir())
    check(len(saved) == 1 and saved[0].endswith(f"-{PORT}-Claude-Fusion-Michael-Sam"), "the game that was replaced was saved first", saved)
    check(tunnels.read_record(LIVE, PORT)["pid"] == rec["pid"] and (LIVE / "sam-link.txt").read_text().strip().endswith(key("Sam")),
          "the tunnel was kept (the same address); the link was proved again and carries the new key")

    print("3. A short deck list stops the launch", flush=True)
    (LIVE / "deck-fusion.txt").write_text("17 Mountain\n20 Starfleet Security\n")
    t1 = pids(PORT).get("table")
    c, out = run("play_four.sh", "--port", PORT, "--force")
    check(c == 1 and "deck-fusion.txt has 37 cards, under 40: not starting" in out and pids(PORT).get("table") == t1 and tunnels.pid_alive(t1),
          "37 cards: not starting, and the running table is untouched", (c, out))

    print("4. play_round.sh --deck Name=FILE", flush=True)
    special = TMP / "special.json"
    special.write_text(json.dumps({"name": "special", "commander": None, "startingLife": 33, "mainBoard": [
        R.shim.entry(dict(L.RAW["Mountain"]), 17), R.shim.entry(dict(L.RAW["Starfleet Security"]), 23)]}))
    c, out = run("play_round.sh", "--single", "--ports", PORT2, "--deck", f"Ann={special}", "--deck", f"Ben={special}", "Ann=bot:Ben=bot", REMOTE_SEAT="")
    life = api(PORT2, "/api/life")[1]
    check(c == 0 and life == {"Ann": 33, "Ben": 33} and api(PORT2, "/api/match")[1] == {"on": False},
          "two seats with no deck in the deck folder play the file they were given (its starting life shows), one game (--single)", (c, out[-500:], life))
    c, out = run("play_round.sh", "--ports", PORT2, "--restart", PORT2, "Ann=bot:Zed=bot", REMOTE_SEAT="")
    check(c == 1 and "missing deck for Ann" in out, "without --deck and without a deck file the launch stops before anything is touched", (c, out))
finally:
    def ours(pid):
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
