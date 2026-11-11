"""The 2026-11-07 security review (S1, S4): the table refuses requests a web page could forge, and serves card art from outside the
repo only. Starts its own server on a free port (no model, no cloud).

    python3 table/tests/security_review_test.py
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


TMP = Path(tempfile.mkdtemp(prefix="secrev-"))
ART = TMP / "art"; ART.mkdir()
(ART / "abc123.jpg").write_bytes(b"\xff\xd8\xff fake jpeg")
(ART / "index.js").write_text('window.CARD_ART={"Test Card":"abc123"};\n')
(TMP / "secret.jpg").write_bytes(b"outside the art dir")
PORT = free_port()
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", NO_GEMMA="1", TABLE_ART_DIR=str(ART), TABLE_HOSTS=".example-tunnel.test,game.example.test",
           TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
env.pop("TABLE_CLOUD", None)
proc = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external", "--token-file", str(TMP / "token"),
                         "--human", "Michael|", "--human", "Sam|"], cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)


def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Host": f"127.0.0.1:{PORT}"}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


try:
    for _ in range(60):
        try:
            if req("GET", "/api/phase")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.5)
    life = lambda: json.loads(req("GET", "/api/life")[1])["Michael"]   # noqa: E731
    l0 = life()
    js = json.dumps({"player": "Michael", "delta": -1, "by": "Michael"})
    print("S1: what a web page could forge against the laptop")
    st, _ = req("POST", "/api/life", js, {"Content-Type": "text/plain", "Origin": "https://evil.example"})
    check(st == 403 and life() == l0, "a cross-site 'simple' POST (text/plain, foreign Origin) is refused", st)
    st, _ = req("POST", "/api/life", js, {"Content-Type": "text/plain", "Origin": "null"})
    check(st == 403 and life() == l0, "Origin: null (a sandboxed frame) is refused", st)
    st, _ = req("POST", "/api/life", js, {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"})
    check(st == 403 and life() == l0, "Sec-Fetch-Site: cross-site is refused", st)
    st, _ = req("POST", "/api/life", json.dumps({"player": "Michael", "delta": 0, "by": "Michael"}), {"Content-Type": "application/x-www-form-urlencoded"})
    check(st == 200, "a tool on the laptop (no Origin, any content type) still works", st)
    st, _ = req("POST", "/api/life", js, {"Content-Type": "application/json", "Origin": "https://evil.example"})
    check(st == 403 and life() == l0, "a POST from another web page's Origin is refused", st)
    st, _ = req("GET", "/api/life", None, {"Host": "rebind.evil.example:%d" % PORT})
    check(st == 403, "a request addressed to a foreign host name (DNS rebinding) is refused, GET included", st)
    st, _ = req("POST", "/api/life", js, {"Content-Type": "application/json"})
    check(st == 200 and life() == l0 - 1, "the laptop's own JSON POST still works", st)
    st, _ = req("POST", "/api/life", json.dumps({"player": "Michael", "delta": 1, "by": "Michael"}), {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    check(st == 200, "a same-origin page POST still works", st)
    for host in ("localhost:%d" % PORT, "192.168.1.20:%d" % PORT, "abc.example-tunnel.test", "game.example.test"):
        st, _ = req("GET", "/api/phase", None, {"Host": host})
        check(st == 200, f"allowed host: {host}", st)
    st, _ = req("GET", "/api/phase", None, {"Host": "other.example.test"})
    check(st == 403, "a host not in TABLE_HOSTS is refused", st)
    st, _ = req("POST", "/api/chat/photo", b"\xff\xd8\xff", {"Content-Type": "image/jpeg", "X-By": "Michael"})
    check(st != 403, "a raw photo upload from the laptop is not blocked", st)
    print("S4: card art comes from TABLE_ART_DIR, never from the repo")
    st, data = req("GET", "/art/abc123.jpg")
    check(st == 200 and data.startswith(b"\xff\xd8"), "/art/<id>.jpg serves from the art folder", st)
    st, data = req("GET", "/art/index.js")
    check(st == 200 and b"CARD_ART" in data, "/art/index.js serves the name index", st)
    st, _ = req("GET", "/art/../secret.jpg")
    check(st != 200, "no way out of the art folder", st)
    st, _ = req("GET", "/assets/art/abc123.jpg")
    check(st != 200, "nothing under /assets/art (the repo) any more", st)
    if (HERE.parent / ".git").exists():                  # a ZIP download has no repository to ask
        ign = subprocess.run(["git", "check-ignore", "-q", "table/assets/art/x.jpg"], cwd=HERE.parent).returncode == 0
        check(ign, "git ignores table/assets/art/ (art can't be committed by accident)")
    else:
        print("  ⏭️  skipped: not a git checkout, so there is no .gitignore to ask")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
print(f"\n{'all passed' if not FAILS else str(len(FAILS)) + ' failed: ' + '; '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
