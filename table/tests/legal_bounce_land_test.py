"""/api/seat/legal and bounce lands (Dimir Aqueduct, Azorius Chancery: "When this land enters, return a land you control to its
owner's hand"). The engine refuses that land drop without the choice of which land returns, and a listed move must be sendable as
it stands. In the first public agent game (2026-11-12) the list offered {"name": "Dimir Aqueduct"} with no choice: every agent
that trusted the list was refused, and one resent it 1,223 times.

  - with no other land, the plain entry is listed, says the land returns itself, and is accepted
  - with other lands, there is one entry per land it could return (one per name and tapped state, a tapped one first) and a last
    one for itself; none without a choice; the first is accepted and does what its `why` says

    python3 table/tests/legal_bounce_land_test.py
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
DECKS = HERE.parent / "decks"
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(detail)[:400]}"), flush=True)
    if not ok:
        FAILS.append(what)


s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="bounce-"))
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_NO_VOICE="1", TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code",
           REPLIES="template", NO_GEMMA="1", TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
srv = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external", "--fair-seed", "off",
                        "--seed", "11", "--token-file", str(TMP / "token"), "--priority-window", "0",
                        "--ai", "North|", "--ai-deck", str(DECKS / "nghathrod.json"), "--ai", "East|", "--ai-deck", str(DECKS / "tuvasa.json"),
                        "--pilot", "North", "--pilot", "East"], cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)


def req(method, path, body=None, key=None, host=False):
    h = {"Content-Type": "application/json"}
    if key:
        h["X-Seat-Key"] = key
    if host:
        h["X-Brain-Token"] = (TMP / "token").read_text().strip()     # the table's host, at the laptop: sets the scene
    else:
        h["X-Forwarded-For"] = "203.0.113.7"                        # an outside agent
    r = urllib.request.Request(B + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def lands(seat, key):
    c, L = req("GET", f"/api/seat/legal?seat={seat}", key=key)
    return [a for a in L.get("actions") or [] if a["action"] == "land"], L


def to_main(seat, keys, limit=120):
    """Walk the turn with listed moves only until `seat` is in its main 1."""
    for _ in range(limit):
        ph = req("GET", "/api/phase")[1]
        if ph.get("player") == seat and ph.get("step") == "main 1":
            return True
        moved = False
        for n, k in keys.items():
            L = req("GET", f"/api/seat/legal?seat={n}", key=k)[1]
            acts = {a["action"]: a for a in L.get("actions") or []}
            for want in ("keep", "begin", "pass", "end", "next") if n != seat or ph.get("player") != seat else ("keep", "begin", "pass", "next"):
                if want in acts:
                    moved = req("POST", "/api/brain/" + want, acts[want]["body"], key=k)[0] == 200 or moved
                    break
        if not moved:
            time.sleep(0.3)
    return False


try:
    for _ in range(120):
        try:
            if req("GET", "/api/phase")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    keys = {n: req("POST", "/api/seat/claim", {"name": n})[1].get("key", "") for n in ("North", "East")}
    check(all(keys.values()), "two seats, two keys")
    req("POST", "/api/phase/next", {"by": "North", "key": keys["North"]}, key=keys["North"])
    check(to_main("North", keys), "North reaches its first main phase", req("GET", "/api/phase")[1])

    print("no other land: the plain entry, and it says what will happen")
    st = req("GET", "/api/brain/state?seat=North", key=keys["North"])[1]
    for c in [c["name"] for c in st.get("hand") or [] if c.get("land")]:        # an empty board, and only the bounce land to play
        req("POST", "/api/brain/discard", {"seat": "North", "name": c}, host=True)
    c, d = req("POST", "/api/brain/search", {"seat": "North", "name": "Dimir Aqueduct"}, host=True)
    check(c == 200, "the host puts Dimir Aqueduct in North's hand", (c, d))
    ls, L = lands("North", keys["North"])
    aq = [a for a in ls if a["body"]["name"] == "Dimir Aqueduct"]
    check(len(aq) == 1 and "bounce" not in aq[0]["body"] and "itself" in aq[0]["why"], "one entry, no choice to make, and the reason says it returns itself", aq)
    c, d = req("POST", "/api/brain/land", aq[0]["body"], key=keys["North"]) if aq else (0, {})
    check(c == 200 and "itself" in json.dumps(d), "the table accepts it and the land returns itself", (c, str(d)[:200]))

    print("other lands: one entry per land it could return")
    check(to_main("East", keys) and to_main("North", keys), "a turn later North is in its main phase again", req("GET", "/api/phase")[1])
    st = req("GET", "/api/brain/state?seat=North", key=keys["North"])[1]
    for c in [c["name"] for c in st.get("hand") or [] if c.get("land") and c["name"] != "Dimir Aqueduct"]:
        req("POST", "/api/brain/discard", {"seat": "North", "name": c}, host=True)
    for tapped in (False, False, True):
        c, d = req("POST", "/api/brain/search", {"seat": "North", "name": "Island", "to": "battlefield", "tapped": tapped}, host=True)
    c, d = req("POST", "/api/brain/search", {"seat": "North", "name": "Swamp", "to": "battlefield"}, host=True)
    check(c == 200, "the host gives North three Islands (one tapped) and a Swamp", (c, d))
    ls, L = lands("North", keys["North"])
    aq = [a for a in ls if a["body"]["name"] == "Dimir Aqueduct"]
    check(len(aq) == 4 and all(a["body"].get("bounce") for a in aq), "four entries, each with its choice (tapped Island, Island, Swamp, itself): none without", [a["body"] for a in aq])
    check(bool(aq) and "Island (tapped)" in aq[0]["why"] and aq[-1]["body"].get("bounce") == "self", "a tapped land first (it has made its mana), itself last", [a["why"] for a in aq])
    check(len({json.dumps(a["body"], sort_keys=True) for a in aq}) == len(aq), "no two entries are the same request")
    before = req("GET", "/api/brain/state?seat=North", key=keys["North"])[1]
    c, d = req("POST", "/api/brain/land", aq[0]["body"], key=keys["North"]) if aq else (0, {})
    check(c == 200, "the first entry is accepted as listed", (c, str(d)[:300]))
    after = req("GET", "/api/brain/state?seat=North", key=keys["North"])[1]
    names = lambda v: sorted(p["name"] for p in v.get("permanents") or [])
    check(names(after).count("Dimir Aqueduct") == 1 and names(after).count("Island") == names(before).count("Island") - 1
          and [c["name"] for c in after.get("hand") or []].count("Island") == 1, "Dimir Aqueduct is on the battlefield and one Island is back in hand",
          (names(after), [c["name"] for c in after.get("hand") or []]))
    check(not lands("North", keys["North"])[0], "and the land drop is used: no land is listed again this turn")
    check("Traceback" not in (TMP / "log").read_text(), "the table logged no error")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
if FAILS:
    print((TMP / "log").read_text()[-1500:])
sys.exit(1 if FAILS else 0)
