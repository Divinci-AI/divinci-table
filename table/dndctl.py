#!/usr/bin/env python3
"""Play a seat at a D&D table from a terminal: the same HTTP API as the phone page, with this seat's own key.

    export DND_BASE=http://127.0.0.1:8810 DND_NAME=Claude DND_KEY_FILE=~/.divinci/dnd/claude.key
    python table/dndctl.py claim                 # take the seat (the key goes to DND_KEY_FILE, 0600, never printed)
    python table/dndctl.py sheet wizard          # pick a pregen
    python table/dndctl.py watch                 # one line per event, as they happen (for a monitor)
    python table/dndctl.py state                 # scene, party, monsters, initiative, the map
    python table/dndctl.py say "I study the runes on the door."
    python table/dndctl.py ooc "brb"             # out of character: the DM is not called
    python table/dndctl.py roll d20+5 "Arcana"
    python table/dndctl.py move 6 4              # your own token, to a square
    python table/dndctl.py hp -5                 # your own hit points (the DM reports damage; the player applies it)
    python table/dndctl.py init start|next|end
    python table/dndctl.py begin "a premise"
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("DND_BASE", "http://127.0.0.1:8810")
NAME = os.environ.get("DND_NAME", "")
KEY_FILE = os.path.expanduser(os.environ.get("DND_KEY_FILE", ""))


def call(method, path, body=None, timeout=30):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json", "User-Agent": f"dndctl/{NAME}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:   # the table restarted or is gone
        return 0, {"error": f"table unreachable: {type(e).__name__}"}


def post(path, body):
    return call("POST", path, {"by": NAME, "key": open(KEY_FILE).read().strip(), **body})


def line(e) -> str | None:
    t = e.get("type")
    if t == "dm":
        return f"DM: {e['text']}"
    if t == "say":
        return f"{e['by']}{'' if e.get('ic') else ' (ooc)'}: {e['text']}"
    if t == "answer" and e.get("to") == NAME:
        return f"(to you) {e['text']}"
    if t in ("roll", "dnd", "seat") and e.get("text"):
        return f"· {e['text']}"
    return None


def show_state() -> None:
    s = call("GET", "/api/dnd")[1]
    print(f"scene: {s.get('scene')}   DM calls {s.get('dm_calls')}/{s.get('dm_cap')}   busy: {s.get('dm_busy')}")
    for n, sh in s["sheets"].items():
        print(f"  {n}: {sh.get('race')} {sh.get('class')} HP {sh.get('hp')}/{sh.get('max_hp')} AC {sh.get('ac')}"
              f"{' [' + ', '.join(sh['conditions']) + ']' if sh.get('conditions') else ''}")
    for m in s.get("monsters") or []:
        print(f"  monster {m['name']}: HP {m['hp']}/{m['max_hp']} AC {m['ac']}")
    ini = s.get("initiative") or {}
    if ini.get("active"):
        o = ini.get("order") or []
        print(f"  initiative round {ini.get('round')}: " + ", ".join(("▶" if i == ini.get("turn") else "") + f"{x['name']} {x['total']}"
                                                                    for i, x in enumerate(o)) + (f"  pending {ini['pending']}" if ini.get("pending") else ""))
    m = s.get("map") or {}
    tk = m.get("tokens") or {}
    print(f"  map: {m.get('location')}  " + "; ".join(f"{t['name']}({t['x']},{t['y']})" for t in (tk.values() if isinstance(tk, dict) else tk)))


def main() -> None:
    cmd, rest = (sys.argv[1] if len(sys.argv) > 1 else "state"), sys.argv[2:]
    if cmd == "claim":
        code, out = call("POST", "/api/seat/claim", {"name": NAME})
        if code == 200 and out.get("key"):
            fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(out["key"])
            print(f"claimed {NAME}")
        else:
            print(code, out.get("error"))
        return
    if cmd == "state":
        return show_state()
    if cmd == "watch":
        since = call("GET", "/api/events?since=latest")[1].get("last", 0)
        while True:
            code, out = call("GET", f"/api/events?since={since}&wait=20", timeout=30)
            if code != 200:
                time.sleep(2)
                continue
            since = out.get("last", since)
            for e in out.get("events", []):
                ln = line(e)
                if ln:
                    print(ln, flush=True)
    routes = {"say": ("/api/dnd/act", lambda: {"text": " ".join(rest)}),
              "ooc": ("/api/chat", lambda: {"text": " ".join(rest)}),
              "roll": ("/api/dnd/roll", lambda: {"dice": rest[0], "why": " ".join(rest[1:])}),
              "hp": ("/api/dnd/hp", lambda: {"delta": int(rest[0])}),
              "sheet": ("/api/dnd/sheet", lambda: {"pregen": rest[0]}),
              "cond": ("/api/dnd/condition", lambda: {"condition": rest[0], "on": not (len(rest) > 1 and rest[1] == "off")}),
              "init": ("/api/dnd/initiative", lambda: {"action": rest[0] if rest else "start"}),
              "begin": ("/api/dnd/begin", lambda: {"premise": " ".join(rest)})}
    if cmd == "move":
        s = call("GET", "/api/dnd")[1]
        tk = (s.get("map") or {}).get("tokens") or {}
        mine = next((t for t in (tk.values() if isinstance(tk, dict) else tk) if t.get("owner") == NAME), None)
        if not mine:
            return print("no token of yours on the map")
        code, out = post("/api/dnd/map/move", {"token": mine["id"], "x": int(rest[0]), "y": int(rest[1])})
    elif cmd in routes:
        path, body = routes[cmd]
        code, out = post(path, body())
    else:
        return print(__doc__)
    r = out.get("roll")
    print(code, out.get("error") or (f"rolled {r.get('total')} ({r.get('text', '')})" if r else "ok"))


if __name__ == "__main__":
    main()
