"""Under TABLE_OFFLINE=1 nothing is downloaded: the table, table/oracle.py and table/fetch_data.py all refuse (2026-11-14, retro T3).

    PYTHONDONTWRITEBYTECODE=1 python3 table/tests/offline_test.py

Until then a checkout with no table/.cache went online the first time anything was started: the table fetched the card-name list at
start-up and oracle.py fetched the 25 MB bulk file at the first card lookup. A fresh worktree's first test run did exactly that, twice,
on a night when the rule was "nothing leaves the laptop".

This test needs no card data and never touches the network. It copies the table's code (no .cache) into a scratch folder and runs it
there with TABLE_OFFLINE=1. Every child process also gets HTTP(S)_PROXY=http://127.0.0.1:9 (nothing listens there), so code that DID
try to download would fail at once, in a way the checks below tell apart from the refusal: this test fails on the old code without
fetching a byte. The one download in here goes to a stand-in on 127.0.0.1 (the last part: the refusal is the flag's doing).

  1. the table: exits 77 with a line starting "SKIP:" that names `python3 table/fetch_data.py`, and creates no card-names.json
  2. oracle.db() and oracle.token_names(): raise oracle.Unavailable, naming the same command; nothing is written
  3. fetch_data.py, the download command itself: refuses in one line, no traceback, nothing written
  4. the bulk file already on disk, no oracle.json: the rules text is rebuilt from it under TABLE_OFFLINE=1 (no network is needed)
  5. without TABLE_OFFLINE the same fetch_data.py downloads (from the stand-in): the flag is what refuses
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TABLE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import freeport  # noqa: E402

FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(detail)[:500]}"), flush=True)
    if not ok:
        FAILS.append(what)


def scratch() -> Path:
    """The table's own code and nothing else: no .cache, no tests, no pages."""
    root = Path(tempfile.mkdtemp(prefix="offline-"))
    (root / "table").mkdir()
    for f in TABLE.glob("*.py"):
        shutil.copy(f, root / "table" / f.name)
    return root


DEAD = "http://127.0.0.1:9"                             # nothing listens on the discard port: a download attempt is refused at once
BASE_ENV = {k: v for k, v in os.environ.items() if k not in ("TABLE_OFFLINE", "SCRYFALL_BASE", "TYPESAFE_API_KEY", "NO_PROXY", "no_proxy")}
BASE_ENV.update(PYTHONDONTWRITEBYTECODE="1", NO_GEMMA="1", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", TABLE_NO_VOICE="1",
                HTTP_PROXY=DEAD, HTTPS_PROXY=DEAD, http_proxy=DEAD, https_proxy=DEAD)
OFF = {**BASE_ENV, "TABLE_OFFLINE": "1"}


def run(root, argv, env, timeout=60):
    p = subprocess.run([sys.executable, *argv], cwd=root, env=env, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def cache_files(root) -> list[str]:
    c = root / "table" / ".cache"
    return sorted(x.name for x in c.iterdir()) if c.is_dir() else []


ORACLE = ("import sys; sys.path.insert(0, 'table'); import oracle\n"
          "try:\n    {call}\n    print('NO-REFUSAL')\n"
          "except getattr(oracle, 'Unavailable', ()) as e:\n    print('UNAVAILABLE:', e)\n")

print("1. The table does not start by downloading", flush=True)
root = scratch()
rc, out, err = run(root, ["table/server.py", "--any-card", "--port", str(freeport.free_port()), "--brain", "external",
                          "--token-file", str(root / "token")], OFF)
check(rc == 77, "with no card data and TABLE_OFFLINE=1 the table exits 77 (the tests' 'skipped')", (rc, err[-300:]))
line = next((l for l in err.splitlines() if l.startswith("SKIP:")), "")
check("python3 table/fetch_data.py" in line and "TABLE_OFFLINE=1" in line, "…with one line starting SKIP: that names the command to run once", err[-300:])
check("Traceback" not in err and "no network" not in err, "…and it never tried the network (no 'there is no network', no traceback)", err[-300:])
check("card-names.json" not in cache_files(root), "no card-names.json was created", cache_files(root))

print("2. oracle.py does not download", flush=True)
rc, out, err = run(root, ["-c", ORACLE.format(call="oracle.db()")], OFF)
check(rc == 0 and out.startswith("UNAVAILABLE:") and "python3 table/fetch_data.py" in out, "oracle.db() raises oracle.Unavailable, naming the command", (rc, out[-300:], err[-300:]))
rc, out, err = run(root, ["-c", ORACLE.format(call="oracle.card('Sol Ring')")], OFF)
check(rc == 0 and out.startswith("UNAVAILABLE:"), "oracle.card() the same (every lookup goes through db())", (rc, out[-200:], err[-300:]))
rc, out, err = run(root, ["-c", ORACLE.format(call="oracle.token_names()")], OFF)
check(rc == 0 and out.startswith("UNAVAILABLE:"), "oracle.token_names() the same (not a FileNotFoundError)", (rc, out[-200:], err[-300:]))
check(not [f for f in cache_files(root) if f.startswith("oracle")], "nothing was written", cache_files(root))

print("3. The download command itself refuses", flush=True)
rc, out, err = run(root, ["table/fetch_data.py", "--set", "trk"], OFF)
check(rc != 0 and "TABLE_OFFLINE=1 forbids downloading" in err and "unset TABLE_OFFLINE" in err, "fetch_data.py says why, and how to download", (rc, err[-300:]))
check("Traceback" not in err and len(err.strip().splitlines()) == 1, "…in one line, with no traceback", err[-400:])
check(cache_files(root) == [], "…and wrote nothing", cache_files(root))

print("4. A rebuild from the bulk file on disk needs no network", flush=True)
(root / "table" / ".cache").mkdir(exist_ok=True)
(root / "table" / ".cache" / "oracle-cards.raw").write_text(json.dumps([
    {"name": "Zzyx Test Relay", "layout": "normal", "mana_cost": "{2}", "type_line": "Artifact", "oracle_text": "{T}: Add {C}.", "keywords": []},
    {"name": "Zzyx Test Sprite", "layout": "token", "type_line": "Token Creature — Sprite", "oracle_text": "Flying", "power": "1", "toughness": "1"}]))
rc, out, err = run(root, ["-c", "import sys; sys.path.insert(0, 'table'); import oracle; print(oracle.card('Zzyx Test Relay')['type'], oracle.token_names())"], OFF)
check(rc == 0 and out.strip() == "Artifact ['Zzyx Test Sprite']", "oracle.json and the token names are rebuilt from oracle-cards.raw under TABLE_OFFLINE=1", (rc, out[-200:], err[-300:]))
check("oracle.json" in cache_files(root), "…and kept for next time", cache_files(root))
shutil.rmtree(root, ignore_errors=True)

print("5. Without TABLE_OFFLINE the same command downloads (here: from a stand-in on 127.0.0.1)", flush=True)


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": ["Zzyx Test Relay"]} if self.path.startswith("/catalog/card-names") else {}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)


fake = ThreadingHTTPServer(("127.0.0.1", freeport.free_port()), Fake)
threading.Thread(target=fake.serve_forever, daemon=True).start()
root = scratch()
local = {k: v for k, v in BASE_ENV.items() if k.lower() not in ("http_proxy", "https_proxy")}
local.update(SCRYFALL_BASE=f"http://127.0.0.1:{fake.server_port}", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
rc, out, err = run(root, ["table/fetch_data.py", "--no-oracle"], {**local, "TABLE_OFFLINE": "1"})
check(rc != 0 and cache_files(root) == [], "TABLE_OFFLINE=1: refused, though the stand-in would have answered", (rc, err[-200:], cache_files(root)))
rc, out, err = run(root, ["table/fetch_data.py", "--no-oracle"], local)
names = root / "table" / ".cache" / "card-names.json"
check(rc == 0 and names.is_file() and json.loads(names.read_text()) == {"data": ["Zzyx Test Relay"]}, "TABLE_OFFLINE unset: the card names are fetched", (rc, err[-300:]))
fake.shutdown()
shutil.rmtree(root, ignore_errors=True)

print("\nFAILED: " + "; ".join(FAILS) if FAILS else "\nall good")
sys.exit(1 if FAILS else 0)
