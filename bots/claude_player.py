"""Claude's seat at the table, as a player (review S6). Uses ONLY Claude's own seat key (live/claude-seat.key, 0600); it never opens the
table's host token, so it cannot read another seat's hand. What it can see is what any player sees: its own hand and board, the public
board, life, the phase and the game log.

    python3 claude_player.py state                 my hand, mana, my board (private: only my seat)
    python3 claude_player.py board                 the public board, life totals, phase
    python3 claude_player.py log [N]               the last N lines of the game log (default 15)
    python3 claude_player.py do ACTION 'JSON'      one action through my seat, e.g.  do land '{"name":"Island"}'
    python3 claude_player.py pass                  pass priority in the current step
The key is set once by the host at game start: python3 claude_player.py setkey   (reads it from stdin, never from argv).
"""
from __future__ import annotations

import json
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("TABLE_URL", "http://127.0.0.1:8800")
SEAT = os.environ.get("CLAUDE_SEAT", "Claude")
KEYFILE = Path(__file__).resolve().parent / "live" / "claude-seat.key"


def key() -> str:
    st = KEYFILE.stat()
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        sys.exit(f"{KEYFILE} is readable by others: chmod 600 it")
    return KEYFILE.read_text().strip()


def call(method: str, path: str, body=None, seat_key=True):
    h = {"Content-Type": "application/json"}
    if seat_key:
        h["X-Seat-Key"] = key()
    r = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "state"
    if cmd == "setkey":
        k = sys.stdin.readline().strip()
        KEYFILE.parent.mkdir(exist_ok=True)
        fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, k.encode()); os.close(fd)
        print("key stored (0600)"); return
    if cmd == "state":
        c, s = call("GET", f"/api/brain/state?seat={SEAT}")
        if c != 200:
            print(c, s.get("error")); return
        print(f"{SEAT}: life {s.get('life')} · library {s.get('library')} · land played {s.get('land_played')}")
        print("mana:", ", ".join(s.get("mana_sources") or []) or "none untapped")
        for h in s.get("hand") or []:
            print(f"  hand: {h['name']} {h.get('cost','')} · {h.get('type','')}{' · castable' if h.get('castable_now') else ''}")
        for p in s.get("permanents") or []:
            print(f"  #{p['id']} {p['name']} {p.get('pt') or ''}{' tapped' if p.get('tapped') else ''}{' sick' if p.get('sick') else ''}"
                  f"{' +' + str(p['counters']) if p.get('counters') else ''}")
        return
    if cmd == "board":
        b3 = call("GET", "/api/board3d", seat_key=False)[1]
        ph = call("GET", "/api/phase", seat_key=False)[1]
        print(f"phase: {ph.get('player')} · {ph.get('step')} · passes {ph.get('passes')}")
        for s in b3.get("seats", []):
            print(f"{s['name']} ♥{s.get('life')} hand {s.get('hand')} library {s.get('library')} graveyard {s.get('graveyard')}")
            for p in s.get("permanents", []):
                print(f"   #{p.get('id')} {p['name']} {p.get('pt') or ''}{' tapped' if p.get('tapped') else ''}{' +' + str(p['counters']) if p.get('counters') else ''}")
        return
    if cmd == "log":
        n = int(argv[2]) if len(argv) > 2 else 15
        ev, since = [], 0
        while True:
            d = call("GET", f"/api/events?since={since}", seat_key=False)[1].get("events", [])
            if not d:
                break
            ev += d; since = int(d[-1]["id"])
            if len(d) < 250:
                break
        for e in [e for e in ev if e.get("type") in ("say", "life", "phase", "game-over")][-n:]:
            if e.get("type") == "phase" and e.get("kind") == "hold":      # Hold pressed / released: not a step
                print(e.get("id"), "hold", e.get("by") or "", "on" if e.get("on") else "off")
                continue
            print(e.get("id"), e.get("type"), e.get("speaker") or e.get("player") or "", e.get("text") or e.get("step") or e.get("delta") or "")
        return
    if cmd == "pass":
        ph = call("GET", "/api/phase", seat_key=False)[1]
        print(call("POST", "/api/brain/pass", {"seat": SEAT, "player": ph.get("player"), "step": ph.get("step")})); return
    if cmd == "do":
        body = json.loads(argv[3]) if len(argv) > 3 else {}
        print(call("POST", f"/api/brain/{argv[2]}", {"seat": SEAT, **body})); return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
