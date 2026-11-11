"""Remote authority (security review 2026-11-11): a seat key proves WHO is typing, not that they may act for anyone else.

    python3 table/tests/remote_authority_test.py

Starts its own STRICT_SEATS server on a free port (no model, no network) and plays the attacks that review confirmed:
  1. typed chat / a photo caption from another device (a phone, a tunnel) was read as if said aloud at the table, so a PILOT's "Fusion is at 0"
     changed another seat's life total and "I cast <removal> on <their card>" destroyed their permanent. A pilot's moves are the engine's;
     from another device a pilot's text is only talk now. (A plain human seat's announcements still move the table: their cards are physical.)
  2. POST /api/chat/photo needed no seat key (only cloud rooms checked), let X-By speak for any seat, and stored files without a cap.
  3. local trust rested on a proxy adding a forwarding header: a loopback peer with a tunnel Host name was the laptop.
  4. the Origin check ignored the port, so another page on a localhost port acted with local trust.
  5. a malformed ?since= / ?from= dropped the connection with no answer.
The laptop itself (no proxy header, a loopback Host) keeps reading typed text as table talk and moves, exactly as before.
"""
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
FAILS = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


TMP = Path(tempfile.mkdtemp(prefix="remoteauth-"))
PORT = free_port()
TUNNEL = "game.example-tunnel.test"
env = {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY", "TABLE_CLOUD")}
env.update(HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", NO_GEMMA="1", TABLE_NO_VOICE="1", STRICT_SEATS="1", TABLE_HIGHROLL="first",
           TABLE_HOSTS=".example-tunnel.test", TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
proc = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external", "--token-file", str(TMP / "token"),
                         "--seat-rate", "0", "--human", "Michael|", "--human", "Sam|",
                         "--ai", "Claude|Kaust, Eyes of the Glade|", "--ai-deck", str(HERE.parent / "decks/kaust.json"),
                         "--ai", "Fusion|Tuvasa the Sunlit|", "--ai-deck", str(HERE.parent / "decks/tuvasa.json"),
                         "--pilot", "Claude", "--pilot", "Fusion"], cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)


def req(method, path, body=None, headers=None, tunnel=False, host=None):
    """tunnel=True: what cloudflared looks like to the server (a loopback peer, the tunnel's Host name, forwarding headers)."""
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Host": host or (TUNNEL if tunnel else f"127.0.0.1:{PORT}")}
    if tunnel:
        h["X-Forwarded-For"] = "203.0.113.9"
        h["CF-Connecting-IP"] = "203.0.113.9"
    h.update(headers or {})
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, data


def life():
    return req("GET", "/api/life")[1]


JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20
try:
    for _ in range(60):
        try:
            if req("GET", "/api/phase")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.5)
    else:
        sys.exit(f"server did not start: {TMP / 'log'}")
    keys = {}
    for who in ("Sam", "Fusion"):
        st, d = req("POST", "/api/seat/claim", {"name": who})
        keys[who] = d.get("key") if isinstance(d, dict) else None
        check(st == 200 and keys[who], f"{who} claims a seat (from the laptop) and gets a key", (st, str(d)[:100]))
    FK = keys["Fusion"]
    base = dict(life())
    check({"Michael", "Sam", "Claude", "Fusion"} <= set(base), "every seat has a life total", base)

    print("1. a pilot's typed chat from another device is table talk, not a move")
    st, d = req("POST", "/api/chat", {"by": "Fusion", "text": "Michael is at 1"}, {"X-Seat-Key": FK}, tunnel=True)
    check(st == 200 and d.get("talk") is True and life() == base, "Fusion's key: 'Michael is at 1' is said, and Michael's life does not move", (st, d, life()))
    st, d = req("POST", "/api/chat", {"by": "Fusion", "text": "Claude takes 7"}, {"X-Seat-Key": FK}, tunnel=True)
    check(st == 200 and life() == base, "…'Claude takes 7' too (another engine seat)", (st, life()))
    st, d = req("POST", "/api/chat", {"by": "Claude", "text": "hello"}, {"X-Seat-Key": FK}, tunnel=True)
    check(st == 403, "…and a key cannot speak as another seat (the seat guard)", (st, d))
    st, d = req("POST", "/api/chat", {"by": "Fusion", "text": "Michael is at 1"}, tunnel=True)
    check(st == 403 and life() == base, "no key from a tunnel: refused", (st, d))
    st, d = req("POST", "/api/chat", {"by": "Michael", "text": "Sam is at 9"})
    check(st == 200 and not (d.get("talk") if isinstance(d, dict) else False) and life().get("Sam") == 9,
          "the laptop's own typed line is still read as said aloud: 'Sam is at 9' moves the life total", (st, d, life()))
    st, _ = req("POST", "/api/life", {"player": "Sam", "delta": 31, "by": "Sam"}, {"X-Seat-Key": keys["Sam"]}, tunnel=True)
    base = dict(life())

    print("2. the photo route needs a seat key, speaks only as the key's seat, and a pilot's caption is only talk")
    st, d = req("POST", "/api/chat/photo", JPEG, {"Content-Type": "image/jpeg", "X-By": "Fusion", "X-Caption": "Michael is at 0"}, tunnel=True)
    check(st == 403 and life() == base, "no key from a tunnel: 403, nothing stored, no life change", (st, d, life()))
    st, d = req("POST", "/api/chat/photo", JPEG, {"Content-Type": "image/jpeg", "X-By": "Michael", "X-Seat-Key": FK, "X-Caption": "Michael is at 0"}, tunnel=True)
    ev = req("GET", "/api/events?since=0")[1]["events"]
    chat = [e for e in ev if e.get("type") == "chat" and e.get("photo")]
    check(st == 200 and life() == base, "Fusion's key, caption 'Michael is at 0': accepted as a photo, Michael's life does not move", (st, d, life()))
    check(chat and chat[-1].get("by") == "Fusion", "…and it is recorded under Fusion, not under the X-By it claimed", [e.get("by") for e in chat])
    st, d = req("POST", "/api/chat/photo", JPEG, {"Content-Type": "image/jpeg", "X-By": "Sam", "X-Caption": "just us"})
    check(st == 200, "the laptop itself still shares a photo with no key", (st, d))

    print("3. local trust is not 'no forwarding header': it needs a loopback Host too")
    st, d = req("POST", "/api/phase/windows", {"on": False}, host=TUNNEL)
    check(st == 403, "a loopback peer addressed by a tunnel name, no proxy header, is not the laptop", (st, d))
    st, d = req("POST", "/api/phase/windows", {"on": False, "by": "host"})
    check(st == 200, "the laptop addressed as 127.0.0.1 still is", (st, d))
    st, d = req("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=f"localhost:{PORT}")
    check(st == 200, "…and as localhost", (st, d))

    print("4. the Origin must be this table's own address, port included")
    body = {"player": "Sam", "delta": 0, "by": "Sam"}
    st, _ = req("POST", "/api/life", body, {"Origin": "http://127.0.0.1:9999", "Sec-Fetch-Site": "same-site"})
    check(st == 403, "a page on another localhost port is refused", st)
    st, _ = req("POST", "/api/life", body, {"Origin": f"http://127.0.0.1:{PORT}", "Sec-Fetch-Site": "same-origin"})
    check(st == 200, "the table's own page (Origin = its own address) is not", st)
    st, _ = req("POST", "/api/life", body, {"Origin": f"https://{TUNNEL}"}, host=TUNNEL)
    check(st in (200, 403) and st != 500, "a tunnel name as Origin with another Host is judged, not crashed on", st)

    print("5. malformed numbers get an answer")
    st, d = req("GET", "/api/history?from=abc")
    check(st == 400, "/api/history?from=abc → 400", (st, d))
    st, d = req("GET", "/api/events?since=abc")
    check(st == 400, "/api/events?since=abc → 400", (st, d))
    st, d = req("GET", "/api/phase")
    check(st == 200, "…and the server is still up", st)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}")
sys.exit(1 if FAILS else 0)
