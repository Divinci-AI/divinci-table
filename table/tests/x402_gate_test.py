"""The test-mode table fee (x402_gate.py): a seat claim without payment gets 402 with what it costs; a test payment claims it;
a payment can't be reused, forged or underpaid; a remote caller can't mint test payments; real-money mode refuses to start.

    python3 table/tests/x402_gate_test.py
"""
from __future__ import annotations

import base64
import json
import os
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import x402_gate  # noqa: E402

DECKS = cards_path.decks_dir()
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


print("A. the gate on its own")
g = x402_gate.Gate(0.05)
req = g.requirements()
check(req["accepts"][0]["maxAmountRequired"] == "50000" and req["accepts"][0]["network"] == "test", "the 402 body names the price, test network", str(req))
p = g.test_payment("agent-1")
ok, why, rec = g.verify(p)
check(ok and rec.get("amount") == "50000", "a test payment verifies once", why)
check(not g.verify(p)[0] and "already used" in g.verify(p)[1], "the same payment can't be used twice")
other = x402_gate.Gate(0.05).test_payment()
check(not g.verify(other)[0], "a payment minted by another table doesn't verify here")
d = json.loads(base64.urlsafe_b64decode(g.test_payment()))
d["payload"]["amount"] = "1"
check(not g.verify(base64.urlsafe_b64encode(json.dumps(d).encode()).decode())[0], "an edited (underpaid) payment doesn't verify")
check(not g.verify("garbage")[0] and not g.verify(None)[0], "garbage or no header doesn't verify")
for bad in ({"price_usd": 0.05, "mode": "live"}, {"price_usd": 5}):
    try:
        x402_gate.Gate(**bad)
        check(False, f"refused at start: {bad}")
    except SystemExit:
        check(True, f"refused at start: {bad} (real money / more than a small fee)")

print("B. on a table started with --entry-fee 0.05")
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="x402-"))
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", NO_GEMMA="1",
           TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
proc = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external",
                         "--fair-seed", "off", "--token-file", str(TMP / "token"), "--entry-fee", "0.05",
                         "--ai", "Claude|", "--ai-deck", str(DECKS / "claude.json"), "--ai", "Michael|", "--ai-deck", str(DECKS / "sam.json"),
                         "--pilot", "Claude", "--pilot", "Michael"],
                        cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)


def post(path, body, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    r = urllib.request.Request(B + path, method="POST", data=json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


try:
    for _ in range(80):
        try:
            urllib.request.urlopen(B + "/api/phase", timeout=2); break
        except Exception:
            time.sleep(0.5)
    c, d = post("/api/seat/claim", {"name": "Claude"})
    check(c == 402 and d.get("accepts"), "claiming a seat without paying gets 402 and what it costs", f"{c} {d}")
    c, d = post("/api/x402/test-payment", {"payer": "agent"}, {"X-Forwarded-For": "203.0.113.7"})
    check(c in (403, 404) or "x_payment" not in d, "a remote (tunnelled) caller can't mint test payments", f"{c} {d}")
    c, d = post("/api/x402/test-payment", {"payer": "agent"})
    pay = d.get("x_payment")
    check(c == 200 and pay, "the table's own laptop can mint a test payment", f"{c} {d}")
    c, d = post("/api/seat/claim", {"name": "Claude"}, {"X-PAYMENT": pay})
    check(c == 200 and d.get("key") and (d.get("payment") or {}).get("network") == "test", "a paid claim gets the seat and a test receipt", f"{c} {d.get('error')}")
    c, d = post("/api/seat/claim", {"name": "Michael"}, {"X-PAYMENT": pay})
    check(c == 402 and "already used" in str(d.get("error")), "one payment can't buy a second seat", f"{c} {d.get('error')}")
finally:
    proc.terminate()

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
