"""tablectl as a PILOT seat (a virtual deck played over the API with a seat key, as in a cloud room), on a local server
with its own port. No host brain token is used or readable. Checks the key never reaches stdout, is saved 0600, only
the pilot's own seat and life are reachable, and a missing key is refused.

  ~/.venvs/table/bin/python table/tests/pilot_ctl_test.py
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TABLE = Path(__file__).resolve().parents[1]
REPO = TABLE.parent
PORT = 8817
FAILED = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILED.append(name)


def sent(*argv):
    """What tablectl puts in the request body for these arguments (in-process, the request is captured, nothing is sent)."""
    sys.path.insert(0, str(TABLE))
    import tablectl
    got = []
    real, old_argv = tablectl.req, sys.argv
    tablectl.req = lambda method, path, body=None, brain=True: (got.append((path, body)), {"said": [], "public": {}})[1]
    sys.argv = ["tablectl.py", *argv]
    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            tablectl.main()
    finally:
        tablectl.req, sys.argv = real, old_argv
    return got[-1] if got else (None, None)


def payload_checks():
    path, body = sent("activate", "Ransom Note", "--mode", "0", "--index", "0")
    check("tablectl activate --mode 0 --index 0 sends mode 0 and index 0 (0 is a choice, not 'unset')",
          path == "/api/brain/activate" and body.get("mode") == 0 and body.get("index") == 0, body)
    path, body = sent("activate", "Ransom Note", "--mode", "2")
    check("…--mode 2 sends 2, and no index when none was given", body.get("mode") == 2 and "index" not in body, body)
    path, body = sent("activate", "Woe Strider", "--sac", "#12")
    check("tablectl activate --sac REF sends which permanent to sacrifice", body.get("sac") == "#12" and "mode" not in body, body)
    path, body = sent("land", "Gruul Turf", "--bounce", "#7")
    check("tablectl land --bounce REF sends which land returns", path == "/api/brain/land" and body.get("bounce") == "#7", body)
    path, body = sent("land", "Forest")
    check("…and a plain land sends no bounce", "bounce" not in body, body)


def main():
    payload_checks()
    tmp = Path(tempfile.mkdtemp(prefix="pilot-test-"))
    cmd = [sys.executable, str(TABLE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external",
           "--token-file", str(tmp / "token"), "--ai", "Claude|Kaust, Eyes of the Glade|", "--ai-deck", str(REPO / "decks/kaust.json"),
           "--pilot", "Claude", "--human", "Michael|", "--human", "Sam|"]
    env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
    env.update(HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", TABLE_RESEARCH_DIR=str(tmp / "research"))
    log = open(tmp / "server.log", "w")
    proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(180):
            if proc.poll() is not None:
                sys.exit(f"server exited: {tmp / 'server.log'}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/phase", timeout=2).read()
                break
            except (urllib.error.URLError, OSError):
                time.sleep(1)
        keyf = tmp / "seat-key"
        base = {k: v for k, v in os.environ.items() if k not in ("TABLE_SEAT_KEY_FILE", "TABLE_ROOM")}
        e = {**base, "TABLE_URL": f"http://127.0.0.1:{PORT}", "TABLE_SEAT": "Claude", "TABLE_SEAT_KEY_FILE": str(keyf),
             "TABLE_TOKEN_FILE": str(tmp / "no-such-token")}          # the host's token is deliberately unreachable

        def ctl(*a, env=e):
            r = subprocess.run([sys.executable, str(TABLE / "tablectl.py"), *a], capture_output=True, text=True, env=env, timeout=60)
            return r.returncode, r.stdout + r.stderr

        rc, out = ctl("state")
        check("without a key the pilot seat is refused", rc != 0, out[:120])
        rc, out = ctl("claim", "--name", "Claude")
        key = keyf.read_text() if keyf.exists() else ""
        check("claim saves a key", rc == 0 and len(key) >= 16, out[:160])
        check("the key never reaches stdout", key and key not in out)
        check("the key file is private (0600)", keyf.exists() and stat.S_IMODE(keyf.stat().st_mode) == 0o600)
        rc, out = ctl("state")
        check("with the key the pilot sees its own state", rc == 0 and "Claude" in out, out[:160])
        rc, out = ctl("say", "Hello from a pilot seat.")
        check("the pilot can speak at the table", rc == 0, out[:160])
        rc, out = ctl("life", "Michael", "-5")
        check("the pilot cannot change a person's life total", rc != 0 and "own life" in out, out[:160])
        rc, out = ctl("new-game")
        check("the pilot cannot reset the game", rc != 0, out[:160])
        rc, out = ctl("--seat", "Michael", "state")
        check("the pilot's key does not open another seat", rc != 0, out[:160])
        # the key is also what the server has: another claim of the same seat from the same device returns a working key
        ev = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/events?since=0").read())["events"]
        check("the table log shows the line, spoken by the pilot", any(x.get("type") == "say" and "pilot seat" in x.get("text", "") for x in ev))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f"\n{'all passed' if not FAILED else str(len(FAILED)) + ' FAILED: ' + '; '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
