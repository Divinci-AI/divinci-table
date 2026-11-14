"""Regression tests for what slowed down the second Star Trek game (2026-11-07, Michael vs Claude, two pilot seats). Each check names
the retro row it covers (G2-n in ~/.divinci/star-trek-mtg/RETRO-LOG.md).

    python3 table/tests/retro_game2_test.py

Part A drives the engine (player.py) directly. Part B starts its own server on a free port with two pilot seats and checks that a
pilot's turn begins by itself and that the server passes for a seat with nothing to do, but not for one holding an instant.
Needs the Star Trek card data: python3 table/fetch_data.py --set trk."""
from __future__ import annotations

import json
import os
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
cards_path.need_card_data()                            # the table needs the card data, and a test never downloads it (skips without it)
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import player  # noqa: E402
from make_deck_json_shim import entry  # noqa: E402

CARDS = {c["name"]: c for c in json.loads(Path(cards_path.cards_json()).read_text()) if c.get("set") == "trk"}
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


def deck(path: Path, cards: dict[str, int]) -> str:
    path.write_text(json.dumps({"name": path.stem, "commander": None, "startingLife": 20,
                                "mainBoard": [entry(CARDS[n], k) for n, k in cards.items()]}))
    return str(path)


TMP = Path(tempfile.mkdtemp(prefix="retro2-"))
CLAUDE = {"Swamp": 14, "Mountain": 6, "Rogue Artificial Intelligence": 2, "Tal Shiar Operative": 2, "Cha'DIch Investigator": 2}
MICHAEL = {"Plains": 14, "Forest": 6, "Malfunctioning Holodeck": 2, "Tricorder": 2, "Set Phasers to . . .": 2, "Warp Core": 1}


def fresh(name="Claude") -> player.VirtualPlayer:
    vp = player.VirtualPlayer(deck(TMP / f"{name}.json", CLAUDE if name == "Claude" else MICHAEL), name, seed=7)
    vp.hand, vp.battlefield = [], []
    return vp


def onto(vp, name, sick=False, tapped=False):
    c = next(c for c in vp.library if c["name"] == name)
    vp.library.remove(c)
    p = player.Perm(c, sick=sick, tapped=tapped)
    vp.battlefield.append(p)
    return p


print("A. the engine")
vp = fresh()
op = onto(vp, "Tal Shiar Operative")
check(vp.perm(str(op.id)) is op and vp.perm(f"#{op.id}") is op, "G2-7: a permanent can be named by its bare number or #number")
try:
    vp.perm("999")
    check(False, "G2-7: an unknown number is refused")
except player.IllegalAction as e:
    check("#5 or 5" in str(e), "G2-7: the refusal says which forms work", str(e))

vp = fresh()
life0 = vp.life
ai = onto(vp, "Rogue Artificial Intelligence")
vp.todo.clear()
vp.enter_effects(ai, None)
todo = " ".join(vp.todo)
check("You gain 3 life" in todo and "Surveil 2" in todo, "G2-4: a modal enters trigger's reminder lists EVERY mode", todo)
check("(Look at" not in todo, "G2-4: reminder text is left out of the modes", todo)
check(vp.life == life0, "G2-4: no mode runs by itself (the player picks)", f"life {life0} -> {vp.life}")

vm = fresh("Michael")
holo = onto(vm, "Malfunctioning Holodeck")
vm.todo.clear()
vm.enter_effects(holo, None)
check(any("exile target nonland permanent" in t for t in vm.todo), "G2-4: an artifact's \"When this artifact enters\" is read as its enters trigger",
      str(vm.todo))

vt = fresh()                                                # the REAL path: cast from hand (game 9 showed the old direct
for _ in range(3):                                          # enter_effects test passed while casting made no token)
    onto(vt, "Mountain")
vt.library.append(entry(CARDS["Boarding Action"], 1))
vt.hand.append(vt.library.pop())
said = vt.manual_cast("Boarding Action")
toks = [p for p in vt.battlefield if p.token]
check([p.name for p in toks] == ["Klingon Warrior token"] and not toks[0].sick and "Haste" in toks[0].card.get("keywords", []),
      "G3-1: casting a sorcery that says \"Create a 2/1 … token with haste\" makes the token, named for its art, ready to attack",
      f"{said} | {[(p.name, p.sick) for p in toks]}")

va = fresh("Michael")
va.library.append(entry(CARDS["Assembly Specialist"], 1))
asp = player.Perm(va.library.pop())
va.battlefield.append(asp)
va.enter_effects(asp, None)
check((asp.id, "own-token") in getattr(va, "etb_done", set()) and any(p.name == "Borg token" for p in va.battlefield),
      "G3-7: the engine records an enters token it made, so the seat cannot make a second copy by hand", str([p.name for p in va.battlefield]))

vs = fresh("Michael")
for nm in ("Class-F Shuttlecraft", "Horta"):
    vs.library.append(entry(CARDS[nm], 1))
    vs.battlefield.append(player.Perm(vs.library.pop()))
ship, horta = vs.battlefield[-2], vs.battlefield[-1]
check(not ship.is_("Creature"), "G3-9: an unstationed Spacecraft is not a creature")
try:
    vs.station(f"#{ship.id}", f"#{ship.id}")
    check(False, "G3-9: a Spacecraft can't station itself")
except player.IllegalAction:
    check(True, "G3-9: a Spacecraft can't station itself")
said = vs.station(f"#{ship.id}", f"#{horta.id}")
check(horta.tapped and ship.charge == vs.stats(horta)[0] and ship.is_("Creature") and vs.stats(ship) == (2, 4),
      "G3-9: Station taps another creature, adds its power as charge counters; at 6+ the ship is a 2/4 artifact creature",
      f"{said} charge={ship.charge} creature={ship.is_('Creature')}")
try:
    vs.station(f"#{ship.id}", f"#{horta.id}")
    check(False, "G3-9: a tapped creature can't station")
except player.IllegalAction:
    check(True, "G3-9: a tapped creature can't station")
check({"Flying", "Vigilance"} <= set(ship.keywords) and not set(ship.keywords) - {"Flying", "Vigilance"},
      "G3-11: at 6+ charge the Shuttlecraft gains its printed \"6+ | Flying, vigilance\"", str(ship.keywords))
ship.sick = False
vs.manual_attack({f"#{ship.id}": "Claude"})
check(not ship.tapped, "G3-11: …and vigilance means it attacks without tapping", f"tapped={ship.tapped}")
fresh_ship = player.Perm(entry(CARDS["Class-F Shuttlecraft"], 1))
check(fresh_ship.keywords == [], "G3-11: an unstationed ship has none of those keywords", str(fresh_ship.keywords))

print("B. the server (two pilot seats)")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
B = f"http://127.0.0.1:{PORT}"
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", NO_GEMMA="1",
           TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
proc = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external", "--fair-seed", "off",
                         "--token-file", str(TMP / "token"), "--ai", "Claude|", "--ai-deck", deck(TMP / "Claude.json", CLAUDE),
                         "--ai", "Michael|", "--ai-deck", deck(TMP / "Michael.json", MICHAEL), "--pilot", "Claude", "--pilot", "Michael"],
                        cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)
TOKEN = ""


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


def wait_for(pred, secs=15):
    end = time.time() + secs
    while time.time() < end:
        ph = call("GET", "/api/phase")[1]
        if pred(ph):
            return ph
        time.sleep(0.3)
    return call("GET", "/api/phase")[1]


KEYS: dict = {}
try:
    for _ in range(80):
        try:
            TOKEN = (TMP / "token").read_text().strip()
            urllib.request.urlopen(B + "/api/phase", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    KEYS.update({n: call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in ("Claude", "Michael")})
    call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)
    call("POST", "/api/phase/next", {"by": "Claude", "key": KEYS["Claude"]}, key=KEYS["Claude"])     # TABLE_HIGHROLL=first: Claude starts

    # G2-1 + G2-3: nobody presses Begin and nobody passes. Michael holds no instant, so the server passes for him, and Claude's turn
    # begins by itself and walks upkeep and draw to main 1.
    ph = wait_for(lambda p: p.get("player") == "Claude" and p.get("begun") and p.get("step") == "main 1")
    check(ph.get("begun") and ph.get("step") == "main 1", "G2-1/G2-3: a pilot's turn begins by itself and reaches main 1 with no presses",
          f"{ph.get('player')} {ph.get('step')} begun={ph.get('begun')} passes={ph.get('passes')}")

    # G2-6: a life change says why, in the log.
    since = call("GET", "/api/events?since=latest")[1].get("next") or 0
    call("POST", "/api/brain/life", {"seat": "Claude", "delta": 3, "text": "Rogue Artificial Intelligence enters: I gain 3 life."}, key=KEYS["Claude"])
    ev = call("GET", f"/api/events?since={max(0, int(since) - 5)}")[1].get("events", [])
    check(any(e.get("type") == "say" and "I gain 3 life" in (e.get("text") or "") for e in ev), "G2-6: the life action announces its reason",
          str([e.get("text") for e in ev if e.get("type") == "say"][-3:]))

    # G2-3, the other side: Michael now holds a castable instant and untapped Plains, so the server must NOT pass for him.
    call("POST", "/api/brain/search", {"seat": "Michael", "name": "Plains"}); call("POST", "/api/brain/put", {"seat": "Michael", "name": "Plains"})
    call("POST", "/api/brain/search", {"seat": "Michael", "name": "Set Phasers to . . ."})
    code, d = call("POST", "/api/brain/end", {"seat": "Claude"}, key=KEYS["Claude"])
    time.sleep(2.5)
    ph = call("GET", "/api/phase")[1]
    ps = ph.get("passes") or {}
    check(ph.get("player") == "Claude" and "Michael" in (ps.get("need") or []) and "Michael" not in (ps.get("passed") or []),
          "G2-3: a seat holding a castable instant is NOT passed for (it decides)",
          f"end={code} {str(d.get('error'))[:80]} | {ph.get('player')} {ph.get('step')} passes={ps}")
    # G3-7: a missed enters trigger on the seat's OWN permanent, applied through its key: Warp Core's basic-land search.
    call("POST", "/api/brain/search", {"seat": "Michael", "name": "Warp Core"}); call("POST", "/api/brain/put", {"seat": "Michael", "name": "Warp Core"})
    wc = next(p for p in call("GET", "/api/brain/state?seat=Michael", key=KEYS["Michael"])[1].get("permanents", []) if p["name"] == "Warp Core")
    hand0 = len(call("GET", "/api/brain/state?seat=Michael", key=KEYS["Michael"])[1].get("hand") or [])
    c1, d1 = call("POST", "/api/brain/effect", {"seat": "Michael", "kind": "search-basic", "source": f"#{wc['id']}", "name": "Forest"}, key=KEYS["Michael"])
    hand1 = len(call("GET", "/api/brain/state?seat=Michael", key=KEYS["Michael"])[1].get("hand") or [])
    check(c1 == 200 and hand1 == hand0 + 1, "G3-7: a seat applies its own permanent's missed enters land search (to hand, per the card)",
          f"{c1} {d1.get('error')} hand {hand0}->{hand1}")
    c2, d2 = call("POST", "/api/brain/effect", {"seat": "Michael", "kind": "search-basic", "source": f"#{wc['id']}", "name": "Forest"}, key=KEYS["Michael"])
    check(c2 != 200 and "already applied" in str(d2.get("error")), "G3-7: the same enters trigger is refused the second time", f"{c2} {d2.get('error')}")
    pl = next(p for p in call("GET", "/api/brain/state?seat=Michael", key=KEYS["Michael"])[1].get("permanents", []) if p["name"] == "Plains")
    c3, d3 = call("POST", "/api/brain/effect", {"seat": "Michael", "kind": "search-basic", "source": f"#{pl['id']}", "name": "Forest"}, key=KEYS["Michael"])
    check(c3 != 200, "G3-7: a permanent whose text has no such trigger is refused", f"{c3} {d3.get('error')}")
    c4, d4 = call("POST", "/api/brain/station", {"seat": "Michael", "ship": "#1", "crew": "#2"}, key=KEYS["Michael"])
    check(c4 != 200 and "sorcery" in str(d4.get("error")), "G3-9: Station is refused off your own main phase", f"{c4} {d4.get('error')}")
    # G3-8: the passes the server makes for a pilot with nothing to cast must not say so in the public stream.
    evs = call("GET", "/api/events?since=0", host=False)[1].get("events", [])
    pilot_passes = [e for e in evs if e.get("type") == "pass" and e.get("by") in ("Claude", "Michael")]
    check(pilot_passes and not any(e.get("auto") for e in pilot_passes),
          "G3-8: a pilot's automatic pass carries no auto flag in the public stream (it would reveal 'no instant in hand')",
          f"{len(pilot_passes)} pilot passes, {sum(1 for e in pilot_passes if e.get('auto'))} flagged auto")
    # Streaming: /watch shows the table a turn behind. Its events stop at the last turn change and its board is that turn's.
    sp = call("GET", "/api/spectate", host=False)[1]
    live_last = call("GET", "/api/events?since=latest", host=False)[1].get("last") or 0
    call("POST", "/api/brain/say", {"seat": "Claude", "text": "a line spectators must not see yet"}, key=KEYS["Claude"])
    sp2 = call("GET", "/api/spectate", host=False)[1]
    check(sp.get("board") and sp.get("as_of_event", 10 ** 9) <= live_last
          and not any("must not see yet" in (e.get("text") or "") for e in sp2.get("events", []))
          and all(e["id"] <= sp2.get("as_of_event", 0) for e in sp2.get("events", [])),
          "streaming: /api/spectate serves the board and log as of the last turn change, never the turn in progress",
          f"as_of={sp.get('as_of_event')} live={live_last} events={len(sp2.get('events', []))}")
    w = urllib.request.urlopen(B + "/watch", timeout=10)
    page = w.read()
    live_helpers = [x for x in (b"history.js", b"tablebar.js", b"todos.js", b"questions.js", b"urgent.js", b"declare.js") if x in page]
    check(w.status == 200 and b"WATCH" in page and not live_helpers and b"/assets/art.js" in page,
          "streaming: /watch serves the read-only board WITHOUT the helpers that read the live game (they'd undo the delay)",
          f"live helpers present: {live_helpers}")
    check(sp2.get("donate") is None, "streaming: no donation link unless the host sets one", str(sp2.get("donate")))
    # G3-4 (table side): a virtual-only 1v1 table ends itself at 0 life.
    call("POST", "/api/brain/life", {"seat": "Michael", "delta": -60, "text": "test: knocked out"})
    ph = call("GET", "/api/phase")[1]
    check(ph.get("game_over") and ph.get("winner") == "Claude" and ph.get("finish") == ["Claude", "Michael"],
          "G3-4: a virtual-only table declares game over when one player is left (no more turns against an empty seat)",
          f"{ph.get('game_over')} {ph.get('winner')} {ph.get('finish')}")
finally:
    proc.terminate()

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
