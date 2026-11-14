"""lobby.py: host-only and loopback-only; creates 1v1 and 2HG games whose seat keys come back as 0600 FILES (never key text);
two bots play a lobby game from those files; the list shows phase and life; bad requests are refused; the cap holds; stop works.

    python3 table/tests/lobby_test.py
"""
from __future__ import annotations

import json
import os
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
cards_path.need_card_data()                            # the table needs the card data, and a test never downloads it (skips without it)
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("BOT_DIR") or Path(__file__).resolve().parents[2] / "bots").expanduser()
cards_path.need(ROOT / "claude_autopilot.py", "the Claude bot", "BOT_DIR=<dir with claude_autopilot.py>")
DECKS = cards_path.decks_dir()
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
L = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="lobbytest-"))
lobby = subprocess.Popen([sys.executable, str(HERE / "lobby.py"), "--port", str(PORT), "--token-file", str(TMP / "lobby.token"),
                          "--max-tables", "2"], stdout=open(TMP / "lobby.log", "w"), stderr=subprocess.STDOUT)
bots = []


def req(method, path, body=None, token=True, fwd=False):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-Lobby-Token"] = (TMP / "lobby.token").read_text().strip()
    if fwd:
        h["X-Forwarded-For"] = "203.0.113.40"
    r = urllib.request.Request(L + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


try:
    for _ in range(40):
        try:
            urllib.request.urlopen(L + "/health", timeout=2); break
        except Exception:
            time.sleep(0.25)
    check(stat.S_IMODE((TMP / "lobby.token").stat().st_mode) == 0o600, "the lobby token file is private (0600)")
    check(req("GET", "/tables", token=False)[0] == 403, "no token: refused")
    check(req("GET", "/tables", fwd=True)[0] == 403, "a forwarded (tunnelled) request is refused even with the token")
    one = {"seats": [{"name": "Michael", "deck": str(DECKS / "sam.json")}, {"name": "Claude", "deck": str(DECKS / "claude.json")}],
           "windows": False}
    c, raw = req("POST", "/tables", one)
    t1 = json.loads(raw)
    keys = t1.get("keys", {})
    check(c == 200 and set(keys) == {"Michael", "Claude"}, "a 1v1 game is created", raw[:200])
    check(all(stat.S_IMODE(Path(k).stat().st_mode) == 0o600 for k in keys.values()), "seat keys come back as 0600 files")
    check(not any(Path(k).read_text().strip() in raw for k in keys.values()), "no key text in the lobby's reply")
    for n, kf in keys.items():
        bots.append(subprocess.Popen([sys.executable, "-u", str(ROOT / "claude_autopilot.py")], cwd=ROOT,
                                     env={**os.environ, "TABLE_URL": t1["url"], "CLAUDE_SEAT": n, "CLAUDE_KEY_FILE": kf, "AUTOPILOT_GRACE": "2"},
                                     stdout=open(TMP / f"{n}.log", "w"), stderr=subprocess.STDOUT))
    k = Path(keys["Michael"]).read_text().strip()
    r = urllib.request.Request(t1["url"] + "/api/phase/next", method="POST", data=json.dumps({"by": "Michael", "key": k}).encode(),
                               headers={"Content-Type": "application/json", "X-Seat-Key": k})
    urllib.request.urlopen(r, timeout=10).read()
    time.sleep(20)
    lst = json.loads(req("GET", "/tables")[1])["tables"]
    me = next(t for t in lst if t["id"] == t1["id"])
    check(me.get("running") and me.get("player") in ("Michael", "Claude") and isinstance(me.get("life"), dict),
          "the list shows a running game's turn, step and life", str(me)[:200])
    turns = sum(1 for e in json.loads(urllib.request.urlopen(t1["url"] + "/api/events?since=0", timeout=10).read())["events"]
                if e.get("type") == "say" and ", turn " in (e.get("text") or ""))
    check(turns >= 3, "two bots play a lobby game from the key files", f"turns={turns}")
    check(req("POST", "/tables", {"seats": [{"name": "A", "deck": "relative.json"}, {"name": "B", "deck": "/nope.json"}]})[0] == 400,
          "a bad deck path is refused")
    check(req("POST", "/tables", {"seats": [{"name": "A<script>", "deck": str(DECKS / "sam.json")}, {"name": "B", "deck": str(DECKS / "sam.json")}]})[0] == 400,
          "a bad seat name is refused")
    two = {"format": "2hg", "seats": [{"name": n, "deck": str(DECKS / "claude.json")} for n in ("A", "B", "C", "D")],
           "teams": {"Red": ["A", "B"], "Blue": ["C", "D"]}}
    c, raw = req("POST", "/tables", two)
    t2 = json.loads(raw)
    life = json.loads(urllib.request.urlopen(t2["url"] + "/api/life", timeout=10).read()) if c == 200 else {}
    check(c == 200 and set(life.values()) == {30}, "a 2HG game is created with shared 30 life", f"{c} {life}")
    c, raw = req("POST", "/tables", one)
    check(c == 503, "the cap holds (max 2 games here)", f"{c} {raw[:100]}")
    c, _ = req("POST", f"/tables/{t2['id']}/stop")
    time.sleep(1)
    lst = json.loads(req("GET", "/tables")[1])["tables"]
    check(c == 200 and not next(t for t in lst if t["id"] == t2["id"])["running"], "stop ends a game")
    check(not any(Path(k).exists() for k in t2.get("keys", {}).values()) and not Path(t2["keys"]["A"]).parent.joinpath("token").exists(),
          "a stopped game's seat-key files and host token are deleted (S19)")
    c, _ = req("POST", "/tables", one)
    check(c == 200, "after a stop there is room again", str(c))
finally:
    for b in bots:
        b.terminate()
    lobby.terminate()
    lobby.wait(timeout=10)
    time.sleep(1)
    left = subprocess.run(["pgrep", "-f", f"lobby-({t1.get('id', 'x')}|{t2.get('id', 'x') if 't2' in dir() else 'x'})-"],
                          capture_output=True, text=True).stdout.split() if 't1' in dir() else []
    check(not left, "stopping the lobby stops every game it started", f"{len(left)} left")
    check(not any(Path(k).exists() for k in keys.values()), "shutting the lobby down deletes every game's key files too")
    for p_ in left:
        os.kill(int(p_), 15)

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
