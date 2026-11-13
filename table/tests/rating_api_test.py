"""POST /api/openmic/rate: a seated person's 👍/🙄 on an AI's unprompted line. A request that is not shaped right is refused
with a reason, never a 500 (found by the security review of 2026-11-12: {"event": "x"} raised in the handler and wrote a traceback
to the log on every call).

    python3 table/tests/rating_api_test.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
import cards_path  # noqa: E402
if not (HERE / ".cache" / "oracle.json").exists():        # (without it the table would download it: a test never goes to the network)
    cards_path.skip("needs the card rules text: run `python3 table/fetch_data.py` once")
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(detail)[:300]}"), flush=True)
    if not ok:
        FAILS.append(what)


s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
TMP = Path(tempfile.mkdtemp(prefix="rate-"))
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_NO_VOICE="1", TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code",
           REPLIES="template", NO_GEMMA="1", TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
srv = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external", "--fair-seed", "off",
                        "--token-file", str(TMP / "token"), "--human", "Ann|", "--human", "Bo|"],
                       cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)


def post(path, body, raw=None):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method="POST", data=raw if raw is not None else json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.8"})
    try:
        with urllib.request.urlopen(r, timeout=20) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw_ = e.read()
        try:
            return e.code, json.loads(raw_ or b"{}")
        except ValueError:
            return e.code, {"raw": raw_[:200].decode("utf-8", "replace")}


try:
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/phase", timeout=2); break
        except Exception:
            time.sleep(0.25)
    key = post("/api/seat/claim", {"name": "Ann"})[1].get("key", "")
    check(bool(key), "Ann claims her seat")
    check(post("/api/openmic/rate", {"by": "Ann", "key": "wrong", "event": 3, "rating": "up"})[0] == 403, "a rating needs the seat's key")
    check(post("/api/openmic/rate", {"by": "Mallory", "key": key, "event": 3, "rating": "up"})[0] == 403, "and a seat at this table")
    for bad in ("x", None, [1], {"a": 1}, "3.5", ""):
        c, d = post("/api/openmic/rate", {"by": "Ann", "key": key, "event": bad, "rating": "up"})
        check(c == 400 and "error" in d, f"event={bad!r} is refused with a reason", (c, d))
    check(post("/api/openmic/rate", {"by": "Ann", "key": key, "event": 3, "rating": "sideways"})[0] == 400, "an unknown rating is refused")
    c, d = post("/api/openmic/rate", {"by": "Ann", "key": key, "event": 3, "rating": "up"})
    check(c == 200 and d.get("ok") is True, "a real rating is kept", (c, d))
    c, d = post("/api/openmic/rate", {"by": "Ann", "key": key, "event": "4", "rating": "roll"})
    check(c == 200, "a number sent as text is still a number", (c, d))
    rows = [json.loads(l) for l in next((TMP / "r").rglob("ratings.jsonl")).read_text().splitlines()]
    check([(r["event"], r["rating"], r["by"]) for r in rows] == [(3, "up", "Ann"), (4, "roll", "Ann")], "exactly those two are on file", rows)
    check("Traceback" not in (TMP / "log").read_text(), "the table logged no error")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
