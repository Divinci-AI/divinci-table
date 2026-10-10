"""'To deck' from the card menu on /board (Chaos Warp: "the owner shuffles the permanent into their library"), and the
engine's matching move for a pilot seat's own permanents.

    ~/.venvs/table/bin/python table/tests/deck_move_test.py

Part 1 is the engine (player.py) in-process: top / bottom / shuffle, tokens, the commander, Auras falling off.
Part 2 is the server, cloud room (TABLE_CLOUD=1), remote calls (X-Forwarded-For), one server on port 8831
(stage1_guard_test's Server): a person's card goes to their library through /api/card-action with their own seat key,
another person's key is refused exactly as for To graveyard / Exile / To hand, and a pilot's permanent goes to its
library through the host's /api/brain/to-library (the same route, and the same host-only rule, as destroy/exile/bounce).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TABLE_HIGHROLL", "first")
os.environ["TABLE_CLOUD"] = "1"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from player import IllegalAction, Perm, VirtualPlayer  # noqa: E402
from stage1_guard_test import Server  # noqa: E402

PASS = FAIL = 0
DECK = HERE.parent.parent / "decks" / "ellivere.json"


def check(name, ok, detail=""):
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {str(detail)[:300]}"), flush=True)


# ───────────────────────────── part 1: the engine ─────────────────────────────
def engine():
    print("engine: a permanent into its owner's library")
    p = VirtualPlayer(str(DECK), "Claude", seed=1)

    def onto(i=0):
        c = next(c for c in p.library if "Land" not in (c.get("type") or "") and c["name"] != p.commander["name"])
        p.library.remove(c)
        x = Perm(c, sick=False)
        p.battlefield.append(x)
        return x

    x = onto()
    n = len(p.library)
    said = p.move(f"#{x.id}", "library", where="top")
    check("top: it leaves the battlefield", x not in p.battlefield, p.battlefield)
    check("top: the library grows by one", len(p.library) == n + 1, (n, len(p.library)))
    check("top: it is the next card drawn", (p.draw(1) or True) and p.hand[-1]["name"] == x.name, p.hand[-1]["name"])
    check("top: the line says so", "on top of my library" in said[0], said)

    x = onto()
    n = len(p.library)
    said = p.move(f"#{x.id}", "library", where="bottom")
    check("bottom: the library grows by one, the card is at the bottom", len(p.library) == n + 1 and p.library[0] is x.card,
          (n, len(p.library), p.library[0]["name"]))
    p.draw(1)
    check("bottom: the next draw is NOT it", p.hand[-1] is not x.card, p.hand[-1]["name"])
    check("bottom: the line says so", "bottom of my library" in said[0], said)

    x = onto()
    before = [c["name"] for c in p.library]
    n = len(p.library)
    said = p.move(f"#{x.id}", "library", where="shuffle")
    check("shuffle: the library grows by one and holds it", len(p.library) == n + 1 and x.card in p.library, (n, len(p.library)))
    check("shuffle: the library is shuffled (not just appended)",
          [c["name"] for c in p.library][:-1] != before or p.library[-1] is not x.card, "unchanged order")
    check("shuffle: the line says so", "shuffled into my library" in said[0], said)

    p.make_tokens("Knight", 2, 2, [], n=1)
    tok = p.battlefield[-1]
    n = len(p.library)
    said = p.move(f"#{tok.id}", "library", where="shuffle")
    check("a token ceases to exist: not on the battlefield, not in the library", tok not in p.battlefield and len(p.library) == n,
          (len(p.library), n))
    check("…and the line says it's gone", "token" in said[0], said)

    cm = Perm(p.commander, sick=False)
    p.battlefield.append(cm)
    p.cmdr_in_zone = False
    n = len(p.library)
    said = p.move(f"#{cm.id}", "library", where="top")
    check("the commander goes to the command zone, not the library",
          p.cmdr_in_zone and len(p.library) == n and p.commander not in p.library, (p.cmdr_in_zone, n, len(p.library)))
    check("…and the line says so", "command zone" in said[0], said)

    x = onto()
    aura = next(c for c in p.library if "Aura" in (c.get("type") or ""))
    p.library.remove(aura)
    a = Perm(aura, sick=False, attached_to=x.id)
    p.battlefield.append(a)
    g, n = len(p.graveyard), len(p.library)
    p.move(f"#{x.id}", "library", where="top")
    check("an Aura on it falls off to the graveyard; only the card itself goes into the library",
          a not in p.battlefield and len(p.graveyard) == g + 1 and p.graveyard[-1] is aura and len(p.library) == n + 1,
          (len(p.graveyard), g, len(p.library), n))

    x = onto()
    try:
        p.move(f"#{x.id}", "library", where="middle")
        ok = False
    except IllegalAction:
        ok = True
    check("a spot that isn't top / bottom / shuffle is refused, and the card stays", ok and x in p.battlefield)


# ───────────────────────────── part 2: the server ─────────────────────────────
def seat_of(S, name):
    c, d = S.call("GET", "/api/board3d")
    assert c == 200, (c, d)
    return next(x for x in d["seats"] if x["name"] == name)


def history_texts(S):
    c, d = S.call("GET", "/api/history")
    rows = d.get("events") if isinstance(d, dict) else d
    return [str(r.get("text", "")) for r in (rows or []) if isinstance(r, dict)]


def people(S):
    print("server: a person's card to their library (/api/card-action)")
    k = S.claim("Michael")
    board = ["Hunted Horror", "Llanowar Elves", "Sol Ring", {"name": "Knight", "token": True}]
    c, d = S.call("POST", "/api/my-board", {"by": "Michael", "key": k, "permanents": board, "graveyard": []}, key=k)
    assert c == 200, (c, d)

    def act(i, name, action, key=k, seat="Michael"):
        return S.call("POST", "/api/card-action", {"seat": seat, "key": key, "index": i, "name": name, "action": action}, key=key)

    for i, (name, action, words) in enumerate([("Hunted Horror", "library-shuffle", "Michael puts Hunted Horror into Michael's library (shuffled in)."),
                                               ("Llanowar Elves", "library-top", "Michael puts Llanowar Elves on top of Michael's library."),
                                               ("Sol Ring", "library-bottom", "Michael puts Sol Ring on the bottom of Michael's library.")]):
        c, d = act(0, name, action)
        names = [p["name"] for p in seat_of(S, "Michael")["permanents"]]
        check(f"{action}: accepted", c == 200, (c, d))
        check(f"{action}: {name} is off the board", name not in names, names)
        check(f"{action}: the log line reads “{words}”", d.get("text") == words and words in history_texts(S), (d, history_texts(S)[-3:]))
        check(f"{action}: it is not put in the graveyard", name not in seat_of(S, "Michael")["graveyard"], seat_of(S, "Michael")["graveyard"])

    c, d = act(0, "Knight", "library-top")
    check("a token: it vanishes from the board", c == 200 and seat_of(S, "Michael")["permanents"] == [], (c, d, seat_of(S, "Michael")["permanents"]))
    check("…and the line says it's gone", "a token: it's gone" in d.get("text", ""), d)

    print("server: someone else's card")
    S.call("POST", "/api/my-board", {"by": "Michael", "key": k, "permanents": ["Hunted Horror"], "graveyard": []}, key=k)
    ks = S.claim("Sam")
    ref = {}
    for action in ("graveyard", "library-shuffle", "library-top", "library-bottom"):
        ref[action] = act(0, "Hunted Horror", action, key=ks)
    check("To graveyard on Michael's card with Sam's key is refused (the existing rule)", ref["graveyard"][0] == 403, ref["graveyard"])
    for action in ("library-shuffle", "library-top", "library-bottom"):
        check(f"{action} on Michael's card with Sam's key is refused the same way",
              ref[action][0] == ref["graveyard"][0] and ref[action][1].get("error") == ref["graveyard"][1].get("error"), (ref[action], ref["graveyard"]))
    check("…and Hunted Horror is still on Michael's board", [p["name"] for p in seat_of(S, "Michael")["permanents"]] == ["Hunted Horror"],
          seat_of(S, "Michael")["permanents"])
    c, d = act(0, "Hunted Horror", "library-top", key=None)
    check("no key at all: refused", c == 403, (c, d))
    c, d = act(0, "Hunted Horror", "library-sideways")
    check("an unknown spot is an unknown action (400), the card stays", c == 400 and seat_of(S, "Michael")["permanents"], (c, d))
    c, d = S.call("POST", "/api/card-action", {"seat": "Claude", "key": S.claim("Claude"), "index": 0, "action": "library-top"},
                  key=S.claim("Claude"))
    check("a pilot's own card is not a /board card action (the AI seats play their own), as for the other moves",
          c in (400, 403), (c, d))


def pilots(S):
    print("server: a pilot's permanent to its library (the host's /api/brain/to-library)")
    st = S.state("Claude")
    hand = st["hand"]
    names = [h if isinstance(h, str) else h.get("name") for h in hand]

    def put_one(skip=()):
        nm = next(n for n in names if n not in skip)
        c, d = S.host_brain("Claude", "put", name=nm)
        assert c == 200, (c, d)
        perm = next(p for p in seat_of(S, "Claude")["permanents"] if p["name"] == nm)
        return nm, perm["id"]

    used = []
    nm, pid = put_one()
    used.append(nm)
    lib0 = seat_of(S, "Claude")["library"]
    c, d = S.brain("Claude", "to-library", ref=f"#{pid}", where="top")
    check("a pilot's own key may not do it (not a pilot action, like destroy / exile / bounce)", c == 403, (c, d))
    check("…and the card stays", any(p.get("id") == pid for p in seat_of(S, "Claude")["permanents"]))
    c, d = S.host_brain("Claude", "to-library", ref=f"#{pid}", where="top")
    check("host: top accepted", c == 200, (c, d))
    check("top: off the battlefield, library +1", all(p.get("id") != pid for p in seat_of(S, "Claude")["permanents"])
          and seat_of(S, "Claude")["library"] == lib0 + 1, (seat_of(S, "Claude")["library"], lib0))
    c, d = S.host_brain("Claude", "draw", n=1)
    check("top: the next draw is that card", c == 200 and (d.get("private") or {}).get("drew") == [nm], (c, d))

    names = [h if isinstance(h, str) else h.get("name") for h in S.state("Claude")["hand"]]
    nm, pid = put_one(skip=used)
    used.append(nm)
    lib0 = seat_of(S, "Claude")["library"]
    c, d = S.host_brain("Claude", "to-library", ref=f"#{pid}", where="bottom")
    check("bottom: accepted, library +1", c == 200 and seat_of(S, "Claude")["library"] == lib0 + 1, (c, d))
    c, d = S.host_brain("Claude", "draw", n=1)
    check("bottom: the next draw is NOT that card", c == 200 and (d.get("private") or {}).get("drew") not in (None, [], [nm]), (c, d, nm))

    names = [h if isinstance(h, str) else h.get("name") for h in S.state("Claude")["hand"]]
    nm, pid = put_one(skip=used)
    lib0 = seat_of(S, "Claude")["library"]
    c, d = S.host_brain("Claude", "to-library", ref=f"#{pid}", where="shuffle")
    check("shuffle: accepted, library +1, off the battlefield",
          c == 200 and seat_of(S, "Claude")["library"] == lib0 + 1 and all(p.get("id") != pid for p in seat_of(S, "Claude")["permanents"]),
          (c, d, seat_of(S, "Claude")["library"], lib0))

    c, d = S.host_brain("Claude", "token", name="Elf Warrior", power=1, toughness=1)
    assert c == 200, (c, d)
    tok = next(p for p in reversed(seat_of(S, "Claude")["permanents"]) if p.get("token"))
    lib0 = seat_of(S, "Claude")["library"]
    c, d = S.host_brain("Claude", "to-library", ref=f"#{tok['id']}", where="shuffle")
    check("a token vanishes: off the battlefield, the library unchanged",
          c == 200 and all(p.get("id") != tok["id"] for p in seat_of(S, "Claude")["permanents"]) and seat_of(S, "Claude")["library"] == lib0,
          (c, d))
    c, d = S.host_brain("Claude", "to-library", ref="#999999", where="top")
    check("no such permanent: refused", c >= 400, (c, d))

    import subprocess                                   # the host's CLI: tablectl to-library REF top|bottom|shuffle
    c, d = S.host_brain("Claude", "token", name="Elf Warrior", power=1, toughness=1)
    tok = next(p for p in reversed(seat_of(S, "Claude")["permanents"]) if p.get("token"))
    env = {**os.environ, "TABLE_URL": "http://127.0.0.1:8831", "TABLE_SEAT": "Claude", "TABLE_TOKEN_FILE": str(S.token_file)}
    r = subprocess.run([sys.executable, str(HERE.parent / "tablectl.py"), "to-library", f"#{tok['id']}", "bottom", "--quiet"],
                       env=env, capture_output=True, text=True, timeout=60)
    check("tablectl to-library (host token) reaches the same action",
          r.returncode == 0 and all(p.get("id") != tok["id"] for p in seat_of(S, "Claude")["permanents"]), (r.returncode, r.stdout[-200:], r.stderr[-200:]))


def main():
    engine()
    with Server() as S:
        S.claim_all()
        pilots(S)                                         # before the first turn: no priority round to wait on
        people(S)
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
