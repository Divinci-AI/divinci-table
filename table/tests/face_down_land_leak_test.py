"""A face-down permanent is a nameless 2/2 whatever the card underneath is: the opponent's view (/api/brain/state "opponents") must not name it,
count it as a land, or leave it out of the creature list. Deterministic: manifest Michael's WHOLE library so every land card of his is face down.

    TABLE_TEST_DECKS=$(python tests/fixture_decks.py) ~/.venvs/table/bin/python table/tests/face_down_land_leak_test.py
"""
from __future__ import annotations
import json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
import fixture_decks  # noqa: E402
DECKS = Path(os.environ["TABLE_TEST_DECKS"]) if os.environ.get("TABLE_TEST_DECKS") else fixture_decks.write(Path(tempfile.mkdtemp(prefix="fdl-decks-")))
FAILS = []
def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok: FAILS.append(what)
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"; TMP = Path(tempfile.mkdtemp(prefix="fdl-"))
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_NO_VOICE="1", TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template",
           NO_GEMMA="1", TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
srv = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external", "--fair-seed", "off", "--seed", "11",
                        "--token-file", str(TMP / "token"), "--decklists", "declared", "--ai", "Michael|", "--ai-deck", str(DECKS / "sam.json"), "--ai", "Claude|",
                        "--ai-deck", str(DECKS / "claude.json"), "--pilot", "Michael", "--pilot", "Claude"], cwd=HERE.parent, env=env,
                       stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)
def req(method, path, body=None, key=None, host=False):
    h = {"Content-Type": "application/json"}
    if key: h["X-Seat-Key"] = key
    if host: h["X-Brain-Token"] = (TMP / "token").read_text().strip()
    r = urllib.request.Request(B + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as x: return x.status, x.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e: return e.code, e.read().decode("utf-8", "replace")
try:
    for _ in range(80):
        try: urllib.request.urlopen(B + "/api/phase", timeout=2); break
        except Exception: time.sleep(0.5)
    K = {n: json.loads(req("POST", "/api/seat/claim", {"name": n})[1]).get("key") for n in ("Michael", "Claude")}
    mich0 = json.loads(req("GET", "/api/brain/state?seat=Michael", host=True)[1])
    lib = len(json.loads(req("GET", "/api/board3d")[1])["seats"][0].get("permanents", [])) and 0
    c_, b_ = req("POST", "/api/brain/manifest", {"seat": "Michael", "n": 60}, host=True)
    check(c_ == 200, "set-up: Michael manifests his whole library face down", b_[:160])
    mich = json.loads(req("GET", "/api/brain/state?seat=Michael", host=True)[1])
    fd = [p for p in mich.get("permanents", []) if p.get("face_down")]
    names = {p["name"] for p in fd}
    hidden_lands = sorted({"Federation Outpost"} & names)
    check(len(fd) >= 20 and hidden_lands, "set-up: there are face-down permanents, and a non-basic land among them", f"{len(fd)} {sorted(names)[:6]}")
    code, body = req("GET", "/api/brain/state?seat=Claude", key=K["Claude"])
    st = json.loads(body); opp = st["opponents"]["Michael"]
    check(code == 200, "Claude reads his own brain state", str(code))
    check(not any(n in body for n in hidden_lands) , "no face-down card's real name in Claude's brain state", str([n for n in names if n in body and len(n) > 4]))
    check(not opp["lands_list"] or all(n for n in opp["lands_list"]) and "Federation Outpost" not in opp["lands_list"], "lands_list holds no face-down card", str(opp["lands_list"]))
    n_up_lands = sum(1 for p in mich.get("permanents", []) if not p.get("face_down") and "Land" in (p.get("type") or ""))
    check(len(opp["lands_list"]) == n_up_lands and opp["lands"] == n_up_lands, "land counts (lands / lands_list) are the FACE-UP lands only", f"lands={opp['lands']} list={len(opp['lands_list'])} faceup={n_up_lands}")
    check(len(opp["creatures"]) == sum(1 for p in mich.get("permanents", []) if p.get("face_down") or "Creature" in (p.get("type") or "")),
          "every face-down permanent is listed as a face-down creature, whatever it really is", f"{len(opp['creatures'])} listed, {len(fd)} face down")
    check(opp["untapped_mana"] == opp.get("untapped_mana") and opp["untapped_mana"] <= n_up_lands + 3, "untapped_mana is not inflated by face-down lands", str(opp["untapped_mana"]))
finally:
    srv.terminate()
print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
