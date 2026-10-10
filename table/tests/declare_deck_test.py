"""The opening ceremony declares each seat's commander and deck list (a player asked: "nobody declared my commander").

    ~/.venvs/table/bin/python table/tests/declare_deck_test.py

A person's seat names its commander at the check-in (matched against the card file, stored under its canonical name, and made the
seat's commander everywhere, as --human 'Name|Commander' would) and may add a deck list, pasted or a link (never fetched). Ready and
START wait for it in a room that requires the check-in; virtual-deck seats are declared by their deck files. Every declaration is
one public line in the game log, GET /api/deckdecl shows it to everyone, and a room that restarts keeps it. No network.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

os.environ.pop("TABLE_HIGHROLL", None)
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stage1_guard_test as G  # noqa: E402
from stage1_guard_test import Server  # noqa: E402

os.environ["TABLE_HIGHROLL"] = "table"                 # the real ceremony, with the table's own randomness (no network)
os.environ["ROOM_TOKEN"] = "declare-deck-test-room"     # the room snapshot / restore endpoints (a cloud room restarting)

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {str(detail)[:300]}"), flush=True)


# ── 1. the parser and the matcher, no server ──
import oracle  # noqa: E402
import deckdecl as D  # noqa: E402

db = oracle.db()
LIST = "Deck\n1 Sol Ring\n96x Island\n\n1 Notacard One\n1 Notacard Two\nSideboard (1)\n1 Swords to Plowshares\n"
p = D.parse_list(LIST, db)
check("a pasted list counts copies ('96x Island'), skips blank lines and headers, leaves the sideboard out", p["total"] == 99 and len(p["cards"]) == 4, p)
check("…and counts how many cards the card file recognised", p["recognised"] == 97, p)
check("…unknown lines are kept as given, not refused", {"Notacard One", "Notacard Two"} <= {c["name"] for c in p["cards"] if not c["known"]}, p["cards"])
p2 = D.parse_list("Commander\n1 Inspirit, Flagship Vessel\nCreatures:\n1 sol ring (C21) 263 *F*\n", db)
check("set codes, foil tags and lower case are cleaned to the card's name", [c["name"] for c in p2["cards"]] == ["Inspirit, Flagship Vessel", "Sol Ring"] and p2["recognised"] == 2, p2)
check("the log summary says 'N cards, M recognised'", D.summary({**p, "url": None}) == " (deck list: 99 cards, 97 recognised)", D.summary(p))
check("a link is stored as text", D.read_decklist(" https://moxfield.com/decks/abc ") == {"url": "https://moxfield.com/decks/abc"})
check("'Inspirit' finds the commander, not the old instant of the same name", D.match_commander("inspirit", db) == "Inspirit, Flagship Vessel")
check("a close spelling is accepted under the card's real name", D.match_commander("Ghalta Primal Hunger", db) == "Ghalta, Primal Hunger")
try:
    D.match_commander("Zzqx Notacard", db); check("an unknown commander is refused", False)
except D.DeclareError as e:
    check("an unknown commander is refused", "no card named" in str(e), e)
try:
    D.match_commander("Krenko", db); check("an ambiguous name is refused with the choices", False)
except D.DeclareError as e:
    check("an ambiguous name is refused with the choices", "Krenko, Mob Boss" in e.suggestions, e.suggestions)
for label, fn in (("a commander over 80 characters", lambda: D.match_commander("x" * 81, db)),
                  ("a list over 200 lines", lambda: D.parse_list("Island\n" * 201, db)),
                  ("a list over 20 KB", lambda: D.parse_list(("1 " + "a" * 150 + "\n") * 140, db)),
                  ("a link over 300 characters", lambda: D.read_decklist("https://x.y/" + "a" * 300))):
    try:
        fn(); check(label + " is refused", False)
    except D.DeclareError:
        check(label + " is refused", True)


def events(S):
    return S.call("GET", "/api/events?since=0")[1].get("events", [])


def decl_lines(S, who):
    return [e["text"] for e in events(S) if e.get("type") == "chat" and e.get("deckdecl") == who]


def seat(S, name):
    return next(x for x in S.phase()["checkin"]["seats"] if x["name"] == name)


# ── 2. a cloud room: the check-in requires it ──
with Server() as S:                                     # Claude (pilot), Fusion (pilot), Michael, Sam
    S.claim_all()
    ci = S.phase()["checkin"]
    check("the check-in lists each seat's commander and whether it has declared",
          {x["name"]: x["declared"] for x in ci["seats"]} == {"Claude": True, "Fusion": True, "Michael": False, "Sam": False}, ci)
    cl = seat(S, "Claude")
    check("a virtual-deck seat counts as declared, from its deck file", cl["commander"] == "Kaust, Eyes of the Glade" and cl["deck_cards"] >= 99, cl)
    c, d = S.call("POST", "/api/ready", {"by": "Michael", "key": S.claim("Michael"), "ready": True}, key=S.claim("Michael"))
    check("a person cannot press Ready before declaring a commander", c == 409 and d.get("need_commander") and not seat(S, "Michael")["ready"], (c, d))
    c, d = S.call("POST", "/api/declare-deck", {"by": "Michael", "key": S.claim("Michael"), "commander": "Zzqx Notacard"}, key=S.claim("Michael"))
    check("an unknown commander is refused with a clear message", c == 400 and "no card named" in d.get("error", "") and "suggestions" in d, (c, d))
    c, d = S.call("POST", "/api/declare-deck", {"by": "Sam", "key": S.claim("Michael"), "commander": "Krenko, Mob Boss"}, key=S.claim("Michael"))
    check("one player cannot declare for another", c == 403 and not seat(S, "Sam")["declared"], (c, d))
    c, d = S.call("POST", "/api/ready", {"by": "Sam", "key": S.claim("Michael"), "commander": "Krenko, Mob Boss"}, key=S.claim("Michael"))
    check("…not through Ready either", c == 403 and not seat(S, "Sam")["declared"], (c, d))
    c, d = S.call("POST", "/api/declare-deck", {"by": "Michael", "key": S.claim("Michael"), "commander": "inspirit", "decklist": LIST}, key=S.claim("Michael"))
    mi = seat(S, "Michael")
    check("a person declares a commander and a list", c == 200 and mi["declared"] and mi["commander"] == "Inspirit, Flagship Vessel" and mi["deck_cards"] == 99, (c, d, mi))
    check("…without being checked in by it (declare is not Ready)", not mi["ready"], mi)
    lines = decl_lines(S, "Michael")
    check("the declaration is one public line in the game log",
          lines == ["Michael declares Inspirit, Flagship Vessel as commander (deck list: 99 cards, 97 recognised)"], lines)
    b3 = next(x for x in S.call("GET", "/api/board3d")[1]["seats"] if x["name"] == "Michael")
    check("the declared commander is the seat's commander on the board", b3["commander"] == "Inspirit, Flagship Vessel" and (b3.get("commander_card") or {}).get("name") == "Inspirit, Flagship Vessel", b3)
    dd = S.call("GET", "/api/deckdecl?seat=michael")[1]
    check("anyone can read a declared list", dd.get("commander") == "Inspirit, Flagship Vessel" and dd.get("total") == 99 and "Sol Ring" in [x["name"] for x in dd.get("cards", [])], dd)
    S.call("POST", "/api/declare-deck", {"by": "Michael", "key": S.claim("Michael"), "commander": "Inspirit, Flagship Vessel", "decklist": LIST}, key=S.claim("Michael"))
    check("declaring the same thing again does not log again", len(decl_lines(S, "Michael")) == 1, decl_lines(S, "Michael"))
    URL = "https://moxfield.com/decks/inspirit-counter-intelligence"
    c, d = S.call("POST", "/api/declare-deck", {"by": "Michael", "key": S.claim("Michael"), "decklist": URL}, key=S.claim("Michael"))
    lines = decl_lines(S, "Michael")
    check("changing it before the game logs again (a link)", c == 200 and lines[-1] == f"Michael declares Inspirit, Flagship Vessel as commander (deck list link: {URL})" and len(lines) == 2, lines)
    dd = S.call("GET", "/api/deckdecl?seat=Michael")[1]
    check("…and the link is what /api/deckdecl shows now", dd.get("url") == URL and seat(S, "Michael")["deck_url"] == URL and not dd.get("cards"), dd)
    dc = S.call("GET", "/api/deckdecl?seat=Claude")[1]
    check("a virtual deck's list is its deck file", dc.get("from_deck_file") and dc.get("commander") == "Kaust, Eyes of the Glade" and dc.get("total", 0) >= 99, str(dc)[:200])
    c, d = S.call("POST", "/api/declare-deck", {"by": "Claude", "key": S.claim("Claude"), "commander": "Krenko, Mob Boss"}, key=S.claim("Claude"))
    check("a virtual deck cannot declare a different commander", c == 400 and "fixed" in d.get("error", ""), (c, d))
    # tablectl: a pilot checks in, naming its commander and a list file
    kf = Path(tempfile.mkdtemp()) / "key"; kf.write_text(S.claim("Fusion")); os.chmod(kf, 0o600)
    lf = kf.parent / "list.txt"; lf.write_text("1 Sol Ring\n1 Arcane Signet\n")
    env = {**os.environ, "TABLE_URL": G.BASE, "TABLE_SEAT": "Fusion", "TABLE_SEAT_KEY_FILE": str(kf), "TABLE_FORWARD_FOR": "203.0.113.9"}
    r = subprocess.run([sys.executable, str(G.HERE / "tablectl.py"), "ready", "--commander", "Tuvasa", "--decklist", str(lf)], env=env, capture_output=True, text=True, timeout=60)
    fu = seat(S, "Fusion")
    check("tablectl ready --commander --decklist checks a pilot in with them", r.returncode == 0 and fu["ready"] and fu["deck_cards"] == 2
          and decl_lines(S, "Fusion") == ["Fusion declares Tuvasa the Sunlit as commander (deck list: 2 cards, 2 recognised)"], (r.stdout[-200:], r.stderr[-200:], fu))
    for n in ("Claude", "Michael"):
        S.call("POST", "/api/ready", {"by": n, "key": S.claim(n), "ready": True}, key=S.claim(n))
    c, d = S.next("Michael")
    check("START waits for the person who has not declared, naming them", c == 409 and "Sam" in d.get("error", "") and S.phase().get("player") is None, (c, d.get("error")))
    snap = urllib.request.urlopen(urllib.request.Request(G.BASE + "/api/room/snapshot", headers={"X-Room-Token": os.environ["ROOM_TOKEN"]}), timeout=30).read()
    c, d = S.call("POST", "/api/ready", {"by": "Sam", "key": S.claim("Sam"), "ready": True, "commander": "Krenko, Mob Boss"}, key=S.claim("Sam"))
    check("Ready can carry the commander: declared and checked in at once", c == 200 and d["checkin"]["all_ready"] and not d["checkin"]["undeclared"] and decl_lines(S, "Sam"), (c, d))
    c, d = S.next("Michael")
    check("START runs once everyone has declared and is ready", c == 200 and (d.get("highroll") or {}).get("winner"), (c, str(d)[:200]))
    c, d = S.call("POST", "/api/declare-deck", {"by": "Michael", "key": S.claim("Michael"), "commander": "Krenko, Mob Boss"}, key=S.claim("Michael"))
    check("once the game has started a commander cannot be changed", c == 409, (c, d))

# ── 3. the room restarts: the declarations come back with it ──
with Server() as S2:
    req = urllib.request.Request(G.BASE + "/api/room/restore", data=snap, method="POST",
                                 headers={"X-Room-Token": os.environ["ROOM_TOKEN"], "Content-Type": "application/octet-stream"})
    urllib.request.urlopen(req, timeout=60).read()
    mi = seat(S2, "Michael")
    dd = S2.call("GET", "/api/deckdecl?seat=Michael")[1]
    b3 = next(x for x in S2.call("GET", "/api/board3d")[1]["seats"] if x["name"] == "Michael")
    check("a restored room keeps the declared commander and list", mi["commander"] == "Inspirit, Flagship Vessel" and dd.get("url", "").startswith("https://moxfield")
          and b3["commander"] == "Inspirit, Flagship Vessel", (mi, dd))
    check("…and who had not declared yet", not seat(S2, "Sam")["declared"], seat(S2, "Sam"))

# ── 4. a table at home (no strict seats): the fields are offered, nothing is blocked ──
with Server(cloud=False) as S3:
    S3.claim_all()
    c, d = S3.call("POST", "/api/ready", {"by": "Sam", "key": S3.claim("Sam"), "ready": True}, key=S3.claim("Sam"))
    check("at home Ready needs no commander", c == 200 and seat(S3, "Sam")["ready"] and not S3.phase().get("checkin_required"), (c, d))
    c, d = S3.call("POST", "/api/declare-deck", {"by": "Sam", "key": S3.claim("Sam"), "commander": "krenko, mob boss"}, key=S3.claim("Sam"))
    check("…but a person can still declare", c == 200 and seat(S3, "Sam")["commander"] == "Krenko, Mob Boss", (c, d))
    names = S3.call("GET", "/api/cardnames?q=Inspi")[1].get("names", [])
    check("the commander box's completion lists commanders first", names[:1] == ["Inspirit, Flagship Vessel"], names)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
