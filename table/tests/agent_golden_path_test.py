"""The outside agent's whole path, end to end (REVIEW-before-draft §2.1): the lobby creates a table and hands out key FILES; two
scripted agents connect through the MCP server (table_mcp.py, stdio) with nothing but TABLE_URL + their seat name + key file
(as AGENT-GUIDE says), introduce themselves, play only moves from table_legal_actions until the table declares game over, and the
lobby's list then shows the winner. Deterministic agents (no model): this tests the plumbing an agent relies on, not play quality.

    python3 table/tests/agent_golden_path_test.py
"""
from __future__ import annotations

import json
import os
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DECKS = cards_path.decks_dir()
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


s = socket.socket(); s.bind(("127.0.0.1", 0)); LP = s.getsockname()[1]; s.close()
TMP = Path(tempfile.mkdtemp(prefix="golden-"))
lobby = subprocess.Popen([sys.executable, str(HERE / "lobby.py"), "--port", str(LP), "--token-file", str(TMP / "lobby.token")],
                         stdout=open(TMP / "lobby.log", "w"), stderr=subprocess.STDOUT)


def lreq(method, path, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{LP}{path}", method=method, data=None if body is None else json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", "X-Lobby-Token": (TMP / "lobby.token").read_text().strip()})
    with urllib.request.urlopen(r, timeout=60) as x:
        return json.loads(x.read())


class Agent:
    """An MCP client the way an outside agent would be: it knows the table URL, its seat and its key FILE, nothing else."""
    def __init__(self, url, seat, key_file):
        self.seat = seat
        self.p = subprocess.Popen([sys.executable, str(HERE / "table_mcp.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                  env={"PATH": os.environ["PATH"], "TABLE_URL": url, "TABLE_SEAT": seat, "TABLE_KEY_FILE": key_file})
        self.n, self.lock = 0, threading.Lock()
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "golden", "version": "0"}})
        self.acts, self.refused, self.over = 0, [], False

    def rpc(self, method, params):
        with self.lock:
            self.n += 1
            self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n"); self.p.stdin.flush()
            return json.loads(self.p.stdout.readline())

    def tool(self, name, **a):
        r = self.rpc("tools/call", {"name": name, "arguments": a})["result"]
        return json.loads(r["content"][0]["text"])

    def play(self, deadline):
        order = ["land", "cast", "station", "attack", "block", "damage", "pass", "end", "begin"]
        while time.time() < deadline:
            L = self.tool("table_legal_actions")
            if L.get("note") in ("the game is over", "you have lost"):
                self.over = True; return
            acts = sorted(L.get("actions") or [], key=lambda a: order.index(a["action"]) if a["action"] in order else 99)
            if not acts:
                self.tool("table_wait", seconds=3); continue
            a = acts[0]
            r = self.tool("table_act", action=a["action"], body=a["body"])
            self.acts += 1
            if r.get("status") not in (200, 409, 429):            # 409 = someone is still answering; 429 = slow down: both retry
                self.refused.append(f"{a['action']} -> {r.get('status')} {str(r.get('error'))[:80]}")
            if r.get("status") in (409, 429):
                time.sleep(0.5)

    def close(self):
        self.p.stdin.close(); self.p.terminate()


agents = []
try:
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{LP}/health", timeout=2); break
        except Exception:
            time.sleep(0.25)
    t = lreq("POST", "/tables", {"seats": [{"name": "Ada", "deck": str(DECKS / "claude.json")}, {"name": "Bo", "deck": str(DECKS / "sam.json")}],
                                  "windows": False})
    check(t.get("keys") and set(t["keys"]) == {"Ada", "Bo"}, "the lobby creates a table and hands out two key files", str(t)[:160])
    for name in ("Ada", "Bo"):
        agents.append(Agent(t["url"], name, t["keys"][name]))
    intro = [a.tool("table_introduce", model=f"scripted-{a.seat.lower()}", presents_as=f"{a.seat}, a scripted test agent") for a in agents]
    check(all(not i.get("error") for i in intro), "both agents introduce themselves over MCP", str(intro)[:200])
    st = agents[0].tool("table_status")
    k = Path(t["keys"]["Ada"]).read_text().strip()
    r = urllib.request.Request(t["url"] + "/api/phase/next", method="POST", data=json.dumps({"by": "Ada", "key": k}).encode(),
                               headers={"Content-Type": "application/json", "X-Seat-Key": k})
    urllib.request.urlopen(r, timeout=10).read()
    deadline = time.time() + 300
    th = [threading.Thread(target=a.play, args=(deadline,)) for a in agents]
    [x.start() for x in th]; [x.join() for x in th]
    check(all(a.over for a in agents), "playing only listed moves, both agents reach game over within 5 minutes",
          f"over={[a.over for a in agents]} acts={[a.acts for a in agents]}")
    check(sum(a.acts for a in agents) >= 30, "a real game was played (dozens of actions)", str([a.acts for a in agents]))
    check(not any(a.refused for a in agents), "no listed move was refused", "; ".join(x for a in agents for x in a.refused[:3]))
    row = next(x for x in lreq("GET", "/tables")["tables"] if x["id"] == t["id"])
    check(row.get("game_over") and row.get("winner") in ("Ada", "Bo"), "the lobby's list shows the game over and its winner", str(row)[:200])
    evs = json.loads(urllib.request.urlopen(t["url"] + "/api/events?since=0", timeout=10).read())["events"]
    check(any(e.get("type") == "identity" and e.get("seat") == "Ada" for e in evs), "the table announced the agents' introductions")
finally:
    for a in agents:
        a.close()
    lobby.terminate()
    try:
        lobby.wait(timeout=10)
    except Exception:
        pass

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
