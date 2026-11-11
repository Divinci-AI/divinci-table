"""Regression tests for what went wrong in the first Star Trek game (2026-11-06, Michael vs Sam, two pilot seats). Each check names the
retro row it covers (~/.divinci/star-trek-mtg/RETRO-LOG.md).

    ~/.venvs/table/bin/python table/tests/retro_game1_test.py

Part A drives the engine (player.py) directly. Part B starts its own server on a free port (two pilot seats, no model) and replays the
combat that dealt damage twice: an attack trigger untapped the Tactical Officer and the table let it attack a second time.
Needs ~/.divinci/star-trek-mtg/cards.json (the Star Trek card data)."""
from __future__ import annotations

import json
import os
import re
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
sys.path.insert(0, os.path.expanduser("~/.divinci/star-trek-mtg/draft"))
import player  # noqa: E402
from make_deck_json import entry  # noqa: E402

CARDS = {c["name"]: c for c in json.loads(Path(os.path.expanduser("~/.divinci/star-trek-mtg/cards.json")).read_text()) if c.get("set") == "trk"}
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


def deck(path: Path, cards: dict[str, int]) -> str:
    path.write_text(json.dumps({"name": path.stem, "commander": None, "startingLife": 20,
                                "mainBoard": [entry(CARDS[n], k) for n, k in cards.items()]}))
    return str(path)


TMP = Path(tempfile.mkdtemp(prefix="retro1-"))
MICHAEL = {"Federation Outpost": 1, "Plains": 12, "Risa": 1, "Forest": 6, "Xindi Surveyors": 2, "Test of Conviction": 2, "Andromedan Automatons": 3,
           "Tactical Officer": 2, "Amok Time": 1}
SAM = {"Tactical Officer": 3, "Andromedan Automatons": 4, "Plains": 8, "Mountain": 8, "Cantankerous Captain": 2}


def fresh(name="Michael") -> player.VirtualPlayer:
    vp = player.VirtualPlayer(deck(TMP / f"{name}.json", MICHAEL if name == "Michael" else SAM), name, seed=7)
    vp.hand, vp.battlefield = [], []
    return vp


def to_hand(vp, name):
    c = next(c for c in vp.library if c["name"] == name)
    vp.library.remove(c)
    vp.hand.append(c)
    return c


def onto(vp, name, sick=False, tapped=False):
    c = to_hand(vp, name)
    vp.hand.remove(c)
    p = player.Perm(c, sick=sick, tapped=tapped)
    vp.battlefield.append(p)
    return p


print("A. the engine")
vp = fresh()
onto(vp, "Plains")
to_hand(vp, "Federation Outpost")
vp.play_land(next(c for c in vp.hand if c["name"] == "Federation Outpost"))
check(next(p for p in vp.battlefield if p.name == "Federation Outpost").tapped, "row 9: Federation Outpost with ONE other land enters tapped")
vp = fresh()
onto(vp, "Plains"); onto(vp, "Plains")
to_hand(vp, "Federation Outpost")
vp.play_land(next(c for c in vp.hand if c["name"] == "Federation Outpost"))
check(not next(p for p in vp.battlefield if p.name == "Federation Outpost").tapped, "row 9: Federation Outpost with TWO other lands enters untapped")

vp = fresh()
x = onto(vp, "Xindi Surveyors")
life0 = vp.life
to_hand(vp, "Risa")
said = vp.play_land(next(c for c in vp.hand if c["name"] == "Risa"))
risa = next(p for p in vp.battlefield if p.name == "Risa")
check(risa.tapped and vp.life == life0 + 1, "Risa enters tapped and gains 1 life", (risa.tapped, vp.life, said))
check(x.counters == 1 and "Landfall" in said, "landfall: Xindi Surveyors gets a +1/+1 counter when a land enters", (x.counters, said))
vp.land_played = False
to_hand(vp, "Forest")
vp.play_land(next(c for c in vp.hand if c["name"] == "Forest"))
check(x.counters == 2, "landfall again on the next land", x.counters)

vp = fresh()
a = onto(vp, "Andromedan Automatons")
copies = sum(1 for c in vp.library if c["name"] == "Andromedan Automatons")
hand0 = len(vp.hand)
said = " ".join(vp.enter_effects(a, lambda *_: None))
check(len(vp.hand) == hand0 + copies and not any(c["name"] == "Andromedan Automatons" for c in vp.library),
      f"row 8: Andromedan Automatons fetches every other copy ({copies}) and says so", said)

vp = fresh()
for _ in range(3):
    onto(vp, "Plains")
x = onto(vp, "Xindi Surveyors")
to_hand(vp, "Test of Conviction")
said = " ".join(vp.manual_cast("Test of Conviction", modes=[1]))
check(x.counters == 1 and "counter on each creature" in said, "row 10b: Test of Conviction, Battle Stations puts the counters on", said)
vp = fresh()
for _ in range(3):
    onto(vp, "Plains")
to_hand(vp, "Test of Conviction")
h0 = len(vp.hand)
said = " ".join(vp.manual_cast("Test of Conviction", modes=[2]))
check(len(vp.hand) == h0 - 1 + 2 and "I draw 2" in said, "Test of Conviction, Begin Negotiations: I draw two", said)

vp = fresh("Sam")
t = onto(vp, "Tactical Officer"); onto(vp, "Andromedan Automatons"); onto(vp, "Andromedan Automatons")
t.tapped = True
said = vp.total_power_triggers()
check(t.counters == 1 and not t.tapped and said, "row 12: Tactical Officer at total power 8 gets a counter and untaps", said)
vp2 = fresh("Sam")
t2 = onto(vp2, "Tactical Officer"); onto(vp2, "Andromedan Automatons")
check(not vp2.total_power_triggers() and t2.counters == 0, "…and does nothing at total power 5")

# ── B. the server: the combat from the game, through two pilot seats ──
print("B. the server (two pilot seats)")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
B = f"http://127.0.0.1:{PORT}"
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", NO_GEMMA="1",
           TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
proc = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external", "--fair-seed", "off",
                         "--token-file", str(TMP / "token"), "--ai", "Sam|", "--ai-deck", deck(TMP / "Sam.json", SAM),
                         "--ai", "Michael|", "--ai-deck", deck(TMP / "Michael.json", MICHAEL), "--pilot", "Sam", "--pilot", "Michael"],
                        cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)


def call(method, path, body=None, key=None, host=True):
    h = {"Content-Type": "application/json"}
    if key:
        h.update({"X-Seat-Key": key, "X-Forwarded-For": "203.0.113.5", "User-Agent": "test-" + key[:4]})
    elif host:
        h["X-Brain-Token"] = TOKEN
    r = urllib.request.Request(B + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


KEYS: dict = {}


def run(action, seat, **body):
    """A host action that waits for the other seat to pass (it passes for it), as a person at the table would."""
    for _ in range(60):
        code, d = call("POST", f"/api/brain/{action}", {"seat": seat, **body}, key=KEYS.get(seat))   # as the seat's own page sends it
        w = d.get("waiting")
        w = ", ".join(w) if isinstance(w, list) else w
        if code == 409 and w:                                       # as play2hg.py does: the active seat passes first, then the others
            ph = call("GET", "/api/phase")[1]
            who = [x.strip() for x in str(w).split(",") if x.strip() and x.strip() != seat]
            ps = [call("POST", "/api/brain/pass", {"seat": n, "player": ph.get("player"), "step": ph.get("step")}, key=KEYS[n]) for n in who]
            if os.environ.get("RETRO_DEBUG") and _ < 2:
                print("   409", str(d.get("error"))[:100], "| passes:", [(c, str(x.get("error") or "ok")[:70]) for c, x in ps])
            time.sleep(0.1)
            continue
        return code, d
    return 599, {"error": "stuck"}


try:
    for _ in range(80):
        try:
            TOKEN = (TMP / "token").read_text().strip()
            urllib.request.urlopen(B + "/api/phase", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    KEYS.update({n: call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in ("Sam", "Michael")})   # passes count through a seat's key
    dbg = os.environ.get("RETRO_DEBUG")
    call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)    # step timeouts off (as in the game): nothing waits 10 s
    r0 = call("POST", "/api/phase/next", {"by": "Sam", "key": KEYS["Sam"]}, key=KEYS["Sam"])   # TABLE_HIGHROLL=first: Sam starts
    if dbg: print("start", r0[0], str(r0[1])[:200])
    def step(*a, **k):
        r = run(*a, **k)
        if dbg: print(a, r[0], str(r[1].get("error") or r[1].get("said"))[:160], "| phase", call("GET", "/api/phase")[1].get("player"), call("GET", "/api/phase")[1].get("step"))
        return r
    step("begin", "Sam")
    for nm in ("Tactical Officer", "Andromedan Automatons", "Andromedan Automatons"):
        call("POST", "/api/brain/search", {"seat": "Sam", "name": nm}); call("POST", "/api/brain/put", {"seat": "Sam", "name": nm})
    def handover(seat):                                                       # after End: the active seat moves cleanup on to the next player
        for _ in range(12):
            ph = call("GET", "/api/phase")[1]
            if ph.get("player") != seat:
                return
            call("POST", "/api/phase/next", {"by": seat, "key": KEYS[seat]}, key=KEYS[seat])
            time.sleep(0.1)
    step("end", "Sam"); handover("Sam"); step("begin", "Michael"); step("end", "Michael"); handover("Michael"); step("begin", "Sam")   # Sam's creatures lose summoning sickness
    st = call("GET", "/api/brain/state?seat=Sam")[1]
    ids = {p["name"]: p["id"] for p in st["permanents"] if p.get("pt")}
    off = next(p["id"] for p in st["permanents"] if p["name"] == "Tactical Officer")
    auto = next(p["id"] for p in st["permanents"] if p["name"] == "Andromedan Automatons")
    _, mk = call("POST", "/api/seat/claim", {"name": "Michael"}, key=None, host=False) if False else (None, None)
    mkey = KEYS["Michael"]
    code, d = call("POST", "/api/brain/land", {"seat": "Michael", "name": "Plains"}, key=mkey)
    check(code == 409 and "turn" in str(d.get("error", "")), "row 1: Michael's land on Sam's turn is refused", (code, d.get("error")))
    code, d = call("POST", "/api/brain/end", {"seat": "Michael"}, key=mkey)
    check(code == 409, "row 1: Michael's End on Sam's turn is refused", (code, d.get("error")))
    code, d = run("attack", "Sam", assign={f"#{off}": "Michael", f"#{auto}": "Michael"})
    said = " ".join(d.get("said") or [])
    check(code == 200 and "counter" in said, "row 12: the attack trigger fires at the declaration (total power 8)", (code, said or d.get("error")))
    st = call("GET", "/api/brain/state?seat=Sam")[1]
    o = next(p for p in st["permanents"] if p["id"] == off)
    check(not o.get("tapped") and o.get("pt") == "3/2", "…the Officer is a 3/2 and untapped", o)
    code, d = run("attack", "Sam", assign={f"#{off}": "Michael"})
    check(code >= 400 and "already attacking" in str(d.get("error", "")), "row 13 (real cause): the untapped Officer cannot attack a second time",
          (code, d.get("error")))
    life0 = call("GET", "/api/life")[1]["Michael"]
    for nm in ("Tactical Officer", "Andromedan Automatons"):
        call("POST", "/api/brain/block", {"seat": "Michael", "attacker": nm}, key=mkey)
    life1 = call("GET", "/api/life")[1]["Michael"]
    check(life0 - life1 == 6, "row 13: Michael takes 3 + 3 = 6 (the Officer's power at damage, once)", (life0, life1))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

print(f"\n{'all passed' if not FAILS else str(len(FAILS)) + ' failed: ' + '; '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
