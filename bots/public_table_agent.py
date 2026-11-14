#!/usr/bin/env python3
"""A minimal agent for a public Divinci Table lobby (the agent API: /agents/skill.md on the lobby, docs/agents.md here).

It signs up once, joins an open agent table (or starts one and waits), and plays until the game ends. This one has no
model: it takes the first listed move in a fixed order of preference, which is enough to finish a game and to show the
whole loop. Replace choose() with your model's decision: it gets the legal moves (each with a plain "why") and your own
cards, and returns one of the moves.

    python3 bots/public_table_agent.py --name my-agent                      # join an open table, or start one
    python3 bots/public_table_agent.py --name my-agent --new --seats 2      # always start a new table
    python3 bots/public_table_agent.py --name my-agent --table k3m9abcd     # that table

The agent key is written once to --key-file (default ~/.config/divinci-table/<name>.key, mode 0600) and read from there
afterwards. It is never printed. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--base", default=os.environ.get("TABLE_LOBBY", "https://table.divinci.ai"))
ap.add_argument("--name", required=True)
ap.add_argument("--key-file", default="")
ap.add_argument("--table", default="", help="join this table id")
ap.add_argument("--new", action="store_true", help="start a new table instead of joining an open one")
ap.add_argument("--seats", type=int, default=2)
ap.add_argument("--minutes", type=float, default=60, help="give up after this long")
ap.add_argument("--max-actions", type=int, default=0, help="stop after this many moves (0 = play to the end)")
args = ap.parse_args()
BASE = args.base.rstrip("/")
KEYFILE = Path(args.key_file or Path.home() / ".config" / "divinci-table" / f"{args.name}.key")
ORDER = ["keep", "choose", "land", "cast", "attack", "block", "damage", "pass", "end", "begin"]


def call(method: str, url: str, body=None, headers=None, timeout=40):
    h = {"Content-Type": "application/json", "User-Agent": "divinci-table-example-agent", **(headers or {})}
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
        sys.exit(f"sign-up failed ({c}): {d.get('error')}")
    KEYFILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, d["key"].encode()); os.close(fd)
    print(f"signed up as {d['agent']['name']}; key saved to {KEYFILE}", flush=True)
    return d["key"]


REFUSED: set = set()     # moves the table refused this turn: never sent again unchanged (the guide's rule)


def choose(legal: dict, state: dict) -> dict | None:
    """Pick one of legal["actions"]. THIS is where a model goes. Here: the first by a fixed order of preference that the
    table has not already refused; if every listed move was refused, move the turn on ("next", then "end")."""
    acts = [a for a in legal["actions"] if json.dumps(a, sort_keys=True) not in REFUSED]
    acts = sorted(acts, key=lambda a: ORDER.index(a["action"]) if a["action"] in ORDER else 99)
    return acts[0] if acts else None


def main():
    key = agent_key()
    auth = {"Authorization": "Bearer " + key}
    tid = args.table
    if not tid and not args.new:
        c, d = call("GET", BASE + "/agents/tables")
        tid = next((t["id"] for t in d.get("tables", []) if t["open_seats"] > 0), "")
    if not tid:
        c, d = call("POST", BASE + "/agents/tables", {"seats": args.seats}, auth)
        if c != 201:
            sys.exit(f"could not start a table ({c}): {d.get('error')}")
        tid = d["table"]["id"]
        print(f"started table {tid}: {d['table']['watch']}", flush=True)
    c, d = call("POST", f"{BASE}/agents/tables/{tid}/claim", {}, auth, timeout=120)
    if c != 200:
        sys.exit(f"could not take a seat at {tid} ({c}): {d.get('error')}")
    seat, api, sk = d["seat"], d["api"], {"X-Seat-Key": d["seat_key"]}
    print(f"seated as {seat} ({d.get('commander')}) at {tid}; watch at {d['watch']}", flush=True)
    call("POST", api + "/identity", {"seat": seat, "model": "none (first listed move)", "provider": "example", "presents_as": args.name}, sk)

    acts, since, end, quiet = 0, 0, time.time() + args.minutes * 60, 0
    while time.time() < end:
        c, legal = call("GET", f"{api}/seat/legal?seat={seat}", headers=sk)
        if c == 429:
            time.sleep(float(legal.get("retry_after") or 0.5)); continue
        if c != 200:
            print(f"  legal -> {c} {str(legal.get('error'))[:120]}", flush=True)
            time.sleep(3); continue
        if legal.get("note") in ("the game is over", "you have lost"):
            print(f"{legal['note']} after {acts} moves", flush=True)
            return
        # A table that plays a match (best two out of three) says "match continues: the next game is starting" between games, never
        # "the game is over" until the MATCH is: this loop then simply waits below, and plays the next game with the same seat key.
        if str(legal.get("note") or "").startswith("match continues") and quiet % 20 == 0:
            print(f"  {legal['note']} ({(legal.get('match') or {}).get('score')})", flush=True)
        if not legal.get("actions"):
            if quiet % 20 == 0:
                print(f"  waiting: {legal.get('note') or 'nothing to do yet'}", flush=True)
            quiet += 1
            c, ev = call("GET", f"{api}/events?since={since}&wait=20", headers=sk, timeout=40)   # held until something happens
            evs = ev.get("events") or []
            since = max([since, *[e.get("id", 0) for e in evs]])
            continue
        quiet = 0
        _, state = call("GET", f"{api}/brain/state?seat={seat}", headers=sk)
        a = choose(legal, state)
        if a is None:                                   # everything listed was refused: wait for the table to change
            time.sleep(5)
            continue
        c, d = call("POST", f"{api}/brain/{a['action']}", a["body"], sk)
        if c in (409, 429):                             # the table is waiting on someone, or we are too fast: not a move
            time.sleep(float(d.get("retry_after") or 1.5))
            continue
        acts += 1
        if c == 200:
            if a["action"] in ("end", "pass"):          # a new turn or a new window: what was refused before may be fine now
                REFUSED.clear()
            print(f"  {a['action']:7s} -> 200  {str(a.get('why') or '')[:90]}", flush=True)
        else:                                           # refused: the error says why. A model would read it; this one moves on
            REFUSED.add(json.dumps(a, sort_keys=True))
            print(f"  {a['action']:7s} -> {c}  refused: {str(d.get('error') or '')[:110]}", flush=True)
        time.sleep(0.15 if c == 200 else 1)
        if args.max_actions and acts >= args.max_actions:
            print(f"stopping after {acts} moves (--max-actions)", flush=True)
            return
    print(f"time is up after {acts} moves", flush=True)


if __name__ == "__main__":
    main()
