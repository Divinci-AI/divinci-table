#!/usr/bin/env python3
"""The house opponent for a public lobby's agent tables (docs/agents.md).

A visiting agent that starts a table should not have to wait for a second visitor. This watches the lobby's list of
agent tables and takes the LAST open seat of a table that has been waiting a while, then plays that seat with
bots/claude_autopilot.py: rules and heuristics, no model, no spend. It never starts a table and never takes a seat
while two or more are open, so agents that come to play each other still find each other first.

It is an ordinary agent: it uses the public agent API with its own agent key, under the same limits as anyone
(two tables at once, six seats an hour), and it says what it is (`identity`: no model).

    python3 bots/house_agent.py --base https://table.example.com --name "House Autopilot" --key-file /data/house.key

The agent key is read from --key-file (0600). If the file is missing the agent signs up under --name and writes it
(a lobby may reserve house names for its admin: then sign up once with the admin token and put the key in the file).
Seat keys are written to --run-dir (0600), one file per game, and removed when the game ends. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--base", default=os.environ.get("TABLE_LOBBY", "https://table.divinci.ai"))
ap.add_argument("--name", default=os.environ.get("HOUSE_NAME", "House Autopilot"))
ap.add_argument("--key-file", default=os.environ.get("HOUSE_KEY_FILE", ""))
ap.add_argument("--run-dir", default=os.environ.get("HOUSE_RUN_DIR", "/tmp/house-agent"))
ap.add_argument("--wait", type=float, default=float(os.environ.get("HOUSE_WAIT_SECS", "60")), help="a table must have waited this long before the house sits down")
ap.add_argument("--poll", type=float, default=float(os.environ.get("HOUSE_POLL_SECS", "20")))
ap.add_argument("--max-games", type=int, default=int(os.environ.get("HOUSE_MAX_GAMES", "2")))
ap.add_argument("--max-hours", type=float, default=6.5, help="a game's player is stopped after this long, whatever happens")
ap.add_argument("--once", action="store_true", help="one pass over the list, then wait for the games it joined (tests)")
args = ap.parse_args()
BASE = args.base.rstrip("/")
KEYFILE = Path(args.key_file or Path.home() / ".config" / "divinci-table" / "house.key")
RUN = Path(args.run_dir)
RUN.mkdir(parents=True, exist_ok=True)
os.chmod(RUN, 0o700)
GAMES: dict[str, dict] = {}        # table id -> {"proc", "seat", "keyfile", "since"}
ROOMS_PATH = BASE + "/agents/tables"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def call(method: str, url: str, body=None, headers=None, timeout=40):
    h = {"Content-Type": "application/json", "User-Agent": "divinci-table-house-agent", **(headers or {})}
    r = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {"error": raw[:200].decode("utf-8", "replace")}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, {"error": str(e)}


def agent_key() -> str:
    if KEYFILE.exists():
        return KEYFILE.read_text().strip()
    c, d = call("POST", BASE + "/agents/register", {"name": args.name})
    if c != 201:
        sys.exit(f"sign-up as {args.name!r} failed ({c}): {d.get('error')}")
    KEYFILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, d["key"].encode()); os.close(fd)
    log(f"signed up as {d['agent']['name']}; key saved to {KEYFILE}")
    return d["key"]


def age_secs(table: dict) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(table["created"].replace("Z", "+00:00"))).total_seconds()
    except (KeyError, ValueError):
        return 0.0


def wants(table: dict, me: str) -> bool:
    """The house sits down only to complete a table: one seat left, the others taken by someone else, and a real wait behind it."""
    seats = table.get("seats") or []
    return (table.get("status") == "waiting" and table.get("open_seats") == 1 and len(seats) >= 2
            and all(s.get("agent") != me for s in seats) and age_secs(table) >= args.wait)


def sit(tid: str, auth: dict) -> bool:
    if not re.fullmatch(r"[a-z0-9]{8}", str(tid)):      # the id becomes a file name and part of a URL: only the shape the lobby makes
        return False
    c, d = call("POST", f"{BASE}/agents/tables/{tid}/claim", {}, auth, timeout=120)
    if c != 200 or not re.fullmatch(r"[\w .'-]{1,24}", str(d.get("seat") or "")) or not isinstance(d.get("seat_key"), str):
        log(f"table {tid}: no seat ({c}): {str(d.get('error'))[:120]}")
        return False
    seat, kf = d["seat"], RUN / f"{tid}.key"
    fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, d["seat_key"].encode()); os.close(fd)
    api = f"{BASE}/agents/tables/{tid}"
    call("POST", api + "/api/identity", {"seat": seat, "model": "none (rules and heuristics)", "provider": "divinci-table bots/claude_autopilot.py",
                                         "presents_as": f"{args.name}, the house opponent"}, {"X-Seat-Key": d["seat_key"]})
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(RUN), "PYTHONUNBUFFERED": "1", "LANG": "C.UTF-8",
           "TABLE_URL": api, "CLAUDE_SEAT": seat, "CLAUDE_KEY_FILE": str(kf), "AUTOPILOT_GRACE": "1",
           "AUTOPILOT_PACE": os.environ.get("AUTOPILOT_PACE", "3"),       # a third of the player's usual request rate: the lobby has a daily budget
           **({"AUTOPILOT_STATS": "1"} if os.environ.get("AUTOPILOT_STATS") else {})}
    out = open(RUN / f"{tid}.log", "ab", buffering=0)
    proc = subprocess.Popen([sys.executable, "-u", str(HERE / "claude_autopilot.py")], env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    GAMES[tid] = {"proc": proc, "seat": seat, "keyfile": kf, "since": time.time()}
    log(f"table {tid}: seated as {seat} ({d.get('commander')}), playing")
    return True


def reap(listed: dict):
    for tid, g in list(GAMES.items()):
        gone = tid not in listed or listed[tid].get("status") == "over"
        late = time.time() - g["since"] > args.max_hours * 3600
        if g["proc"].poll() is None and not (gone and time.time() - g["since"] > 60) and not late:
            continue
        if g["proc"].poll() is None:
            g["proc"].terminate()
            try:
                g["proc"].wait(timeout=10)
            except subprocess.TimeoutExpired:
                g["proc"].kill()
        g["keyfile"].unlink(missing_ok=True)
        log(f"table {tid}: done ({'the table is closed' if gone else 'time' if late else 'the player stopped'})")
        try:                                            # the player's last words, for whoever reads the house's log
            for line in (RUN / f"{tid}.log").read_text().strip().splitlines()[-2:]:
                log(f"table {tid}:   {line[:160]}")
        except OSError:
            pass
        del GAMES[tid]


def main():
    auth = {"Authorization": "Bearer " + agent_key()}
    c, me = call("GET", BASE + "/agents/me", headers=auth)
    if c != 200:
        sys.exit(f"the lobby does not know this key ({c}): {me.get('error')}")
    name = me["agent"]["name"]
    log(f"house agent {name!r} on {BASE}: at most {args.max_games} games, sits after {args.wait:.0f} s")
    for t in me.get("tables") or []:                    # after a restart: back to the seats it already holds
        if t.get("status") != "over" and len(GAMES) < args.max_games:
            sit(t["id"], auth)
    backoff = args.poll
    while True:
        c, d = call("GET", ROOMS_PATH)
        if c != 200:
            backoff = min(300.0, backoff * 2)           # the lobby is resting or unreachable: ask less often
            log(f"list -> {c} {str(d.get('error'))[:100]}; next look in {backoff:.0f} s")
        else:
            backoff = args.poll
            try:                                        # (whatever the lobby sends, the house keeps running)
                listed = {str(t["id"]): t for t in d.get("tables") or [] if isinstance(t, dict) and "id" in t}
                reap(listed)
                for t in listed.values():
                    if len(GAMES) >= args.max_games:
                        break
                    if t["id"] not in GAMES and wants(t, name):
                        sit(str(t["id"]), auth)
            except Exception as e:  # noqa: BLE001
                log(f"list not understood: {e}")
        if args.once:
            for g in GAMES.values():
                g["proc"].wait()
            reap({})
            return
        time.sleep(backoff)


def stop_all(*_):
    """Stopping the house stops its players too (each is in a session of its own) and removes their seat keys."""
    for g in GAMES.values():
        try:
            os.killpg(g["proc"].pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        g["keyfile"].unlink(missing_ok=True)
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop_all)
    signal.signal(signal.SIGINT, stop_all)
    main()
