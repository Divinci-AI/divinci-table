"""table_mcp.py, driven over stdio like an MCP client would, against a real throwaway table: the handshake, six tools, an agent
playing its first turn from table_legal_actions alone, table_wait returning what happened, and the guard rails (the body can't
pick another seat, only table actions are accepted, a world-readable key file is refused, the key never appears in a reply).

    python3 table/tests/table_mcp_test.py
"""
from __future__ import annotations

import json
import os
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
import socket
import subprocess
import sys
import tempfile
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


s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="mcp-"))
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_NO_VOICE="1", TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code",
           REPLIES="template", NO_GEMMA="1", TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
srv = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external",
                        "--fair-seed", "off", "--seed", "9", "--token-file", str(TMP / "token"),
                        "--ai", "Claude|", "--ai-deck", str(DECKS / "claude.json"), "--ai", "Michael|", "--ai-deck", str(DECKS / "sam.json"),
                        "--pilot", "Claude", "--pilot", "Michael"],
                       cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)


def post(path, body, key=None):
    h = {"Content-Type": "application/json", **({"X-Seat-Key": key} if key else {})}
    r = urllib.request.Request(B + path, method="POST", data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(r, timeout=20) as x:
        return json.loads(x.read() or b"{}")


class Client:
    def __init__(self, key_file):
        self.p = subprocess.Popen([sys.executable, str(HERE / "table_mcp.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True,
                                  env={**os.environ, "TABLE_URL": B, "TABLE_SEAT": "Claude", "TABLE_KEY_FILE": str(key_file)})
        self.n = 0
        self.raw = []

    def rpc(self, method, params=None, notify=False):
        self.n += 1
        msg = {"jsonrpc": "2.0", "method": method, **({} if notify else {"id": self.n}), **({"params": params} if params else {})}
        self.p.stdin.write(json.dumps(msg) + "\n"); self.p.stdin.flush()
        if notify:
            return None
        line = self.p.stdout.readline()
        self.raw.append(line)
        return json.loads(line)

    def tool(self, name, **args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return json.loads(r["content"][0]["text"]), r.get("isError")

    def close(self):
        self.p.stdin.close(); self.p.terminate()


try:
    for _ in range(80):
        try:
            urllib.request.urlopen(B + "/api/phase", timeout=2); break
        except Exception:
            time.sleep(0.5)
    K = {n: post("/api/seat/claim", {"name": n})["key"] for n in ("Claude", "Michael")}
    kf = TMP / "claude.key"
    fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, K["Claude"].encode()); os.close(fd)
    post("/api/phase/windows", {"on": False, "by": "host"})
    post("/api/phase/next", {"by": "Claude", "key": K["Claude"]}, key=K["Claude"])
    time.sleep(3)

    c = Client(kf)
    init = c.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
    c.rpc("notifications/initialized", notify=True)
    check(init["result"]["serverInfo"]["name"] == "divinci-table" and "tools" in init["result"]["capabilities"], "MCP handshake")
    tools = {t["name"] for t in c.rpc("tools/list")["result"]["tools"]}
    check(tools == {"table_status", "table_my_state", "table_legal_actions", "table_act", "table_wait", "table_say", "table_introduce"},
          "seven tools listed", str(tools))
    intro, err = c.tool("table_introduce", model="test-model-1", voice="Moira", voice_rate=3, pronouns="she/her",
                        presents_as="A curious card player who likes a long game.")
    vc = json.loads(urllib.request.urlopen(B + "/api/voice-config", timeout=10).read())
    me_ = next(p for p in vc["ai_players"] if p["name"] == "Claude")
    check(not err and me_.get("voice") == "Moira" and me_.get("voice_rate") == 1.25 and me_.get("pronouns") == "she/her"
          and "curious" in me_.get("presents_as", ""),
          "table_introduce: the AI's own voice, pronouns and self-description reach the pages; pace clamped to a human range",
          str(me_)[:240])
    evs = json.loads(urllib.request.urlopen(B + "/api/events?since=0", timeout=10).read())["events"]
    check(any(e.get("type") == "identity" and e.get("seat") == "Claude" for e in evs), "the introduction is announced to the table (pages re-voice)")
    bad, err = c.tool("table_introduce", model="m", voice_rate="fast")
    check(err, "a non-number pace is refused", str(bad))
    flags = {p["name"]: p.get("agent") for p in json.loads(urllib.request.urlopen(B + "/api/voice-config", timeout=10).read())["ai_players"]}
    check(flags == {"Claude": True, "Michael": False}, "the voice config marks which seats are agents (a model declared) vs people", str(flags))
    long_, err = c.tool("table_say", text="word " * 150)
    check(err and "under 500" in str(long_.get("error")), "a line of table talk over 500 characters is refused", str(long_)[:160])
    st, _ = c.tool("table_status")
    check(st.get("phase", {}).get("player") == "Claude" and len(st.get("seats", [])) == 2, "table_status: the public table", str(st)[:160])
    me, _ = c.tool("table_my_state")
    check(len(me.get("hand") or []) >= 6, "table_my_state: this seat's hand", str(me)[:120])
    c.tool("table_wait", seconds=1)                       # catch up the cursor
    played = []
    for _ in range(6):                                    # an agent's first turn, from the list alone
        L, _ = c.tool("table_legal_actions")
        acts = L.get("actions", [])
        pick = next((a for a in acts if a["action"] in ("land", "cast")), None) or next((a for a in acts if a["action"] == "end"), None)
        if not pick:
            break
        r, err = c.tool("table_act", action=pick["action"], body=pick["body"])
        played.append((pick["action"], r.get("status"), r.get("error")))
        if pick["action"] == "end":
            break
    check(played and all(st_ == 200 for _, st_, _ in played) and played[-1][0] == "end",
          "an agent plays its turn from table_legal_actions + table_act alone", str(played))
    w, _ = c.tool("table_wait", seconds=5)
    check(any(e.get("speaker") == "Claude" for e in w.get("events", [])), "table_wait returns what happened (its own plays)", str(w)[:200])
    r, err = c.tool("table_act", action="land", body={"seat": "Michael", "name": "Plains"})
    check(err and r.get("status") != 200, "a body naming another seat still acts only for this seat (and is refused here)", str(r))
    r, err = c.tool("table_act", action="new-game", body={})
    check(err and "must be one of" in str(r.get("error")), "only table actions are accepted (no host actions)", str(r))
    check(not any(K["Claude"] in line or K["Michael"] in line for line in c.raw), "no seat key appears in any reply")
    c.close()
    os.chmod(kf, 0o644)
    c2 = Client(kf)
    c2.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
    r, err = c2.tool("table_my_state")
    check(err and "chmod 600" in str(r.get("error")), "a world-readable key file is refused", str(r))
    c2.close()
finally:
    srv.terminate()

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
