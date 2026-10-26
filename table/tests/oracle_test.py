"""harness/oracle.py: one planted bug per kind is caught with the right location, a clean game is silent, and every
detector is load-bearing (switch it off and its planted bug goes unseen). SYNTHETIC logs, invented card names.

    python3 table/tests/oracle_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "harness"))
import oracle as O  # noqa: E402

ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


T = [0.0]


def rec(seat, action, hand, body=None, drew=None, life=40, **kw):
    T[0] += 1.0
    return {"ts": T[0], "seat": seat, "action": action, "body": body or {}, "said": [], "drew": drew or [],
            "todo": [], "hand_after": hand, "life": life, **kw}


H0 = ["Zorb Plains", "Quill Peak", "Brindle Elf", "Ashen Tome"]
TEXTS = {"Ashen Tome": "Draw two cards.", "Sun Chalice": "You gain 4 life."}


def clean():
    T[0] = 0.0
    return [rec("A", "mulligan", H0), rec("A", "begin", H0 + ["Wren"], drew=["Wren"]),
            rec("A", "land", ["Quill Peak", "Brindle Elf", "Ashen Tome", "Wren"], {"name": "Zorb Plains"}),
            rec("A", "cast", ["Quill Peak", "Ashen Tome", "Wren"], {"name": "Brindle Elf"}),
            rec("A", "pass", ["Quill Peak", "Ashen Tome", "Wren"], {"quiet": True}),
            rec("A", "end", ["Quill Peak", "Ashen Tome", "Wren"])]


def kinds(brain, events=None, **kw):
    return {f["kind"] for f in O.check_game(events or [], brain, TEXTS, **kw)}


check("a clean game has no findings", not O.check_game([], clean(), TEXTS))
T[0] = 0

b = clean(); b.insert(3, {"ts": 9.5, "seat": "A", "action": "cast", "body": {"name": "Ashen Tome"}, "refused": "illegal", "why": "not enough mana"})
check("refused-but-offered", "refused-but-offered" in kinds(b))

b = clean(); b.insert(4, rec("A", "land", ["Quill Peak", "Ashen Tome", "Wren"], {"name": "Quill Peak"}))
check("accepted-but-illegal: second land", any(f["kind"] == "accepted-but-illegal" and "second land" in f["why"] for f in O.check_game([], b, TEXTS)))

b = clean(); b[3] = rec("A", "cast", ["Quill Peak", "Ashen Tome", "Wren"], {"name": "Ghost Card"})
check("accepted-but-illegal: card not in hand", any("not in the hand" in f["why"] for f in O.check_game([], b, TEXTS)))

b = clean(); b[3] = rec("A", "cast", ["Quill Peak", "Brindle Elf", "Ashen Tome", "Wren"], {"name": "Brindle Elf"})
check("accepted-but-illegal: cast card still in hand", any("still in the hand" in f["why"] for f in O.check_game([], b, TEXTS)))

b = clean(); b.insert(4, rec("A", "cast", ["Quill Peak", "Wren"], {"name": "Ashen Tome"}, drew=[]))
check("no-effect: draw spell drew nothing", "no-effect" in kinds(b))
b2 = clean(); b2.insert(4, rec("A", "cast", ["Quill Peak", "Wren", "X1", "X2"], {"name": "Ashen Tome"}, drew=["X1", "X2"]))
check("no-effect stays quiet when the cards are drawn", "no-effect" not in kinds(b2))

b = clean(); b.insert(4, rec("A", "cast", ["Quill Peak", "Ashen Tome", "Wren"], {"name": "Sun Chalice"}, life=40))
b[4]["hand_after"] = ["Quill Peak", "Ashen Tome", "Wren"]
b[3]["hand_after"] = ["Quill Peak", "Ashen Tome", "Wren", "Sun Chalice"]
check("no-effect: life gain with no life gained", "no-effect" in kinds(b))

b = clean(); b[4] = rec("A", "pass", ["Quill Peak", "Wren"], {"quiet": True})
check("drift: a card vanished from the hand", "drift" in kinds(b))
b = clean(); b[4] = rec("A", "pass", ["Quill Peak", "Ashen Tome", "Wren", "Stowaway"], {"quiet": True})
check("drift: a card appeared during a pass", "drift" in kinds(b))
ev = [{"type": "life", "player": "A", "life": 37, "ts": 5.5, "id": 1}]
check("drift: life differs from the last announced", "drift" in kinds(clean(), ev))

ev = [{"type": "say", "speaker": "A", "text": "Next I will play Ashen Tome.", "ts": 5.0, "id": 77}]
fs = [f for f in O.check_game(ev, clean(), TEXTS) if f["kind"] == "leak"]
check("leak: a hand card named publicly, with the event id", fs and fs[0]["event_id"] == 77)
ev = [{"type": "say", "speaker": "A", "text": "I play Zorb Plains.", "ts": 3.0, "id": 5}]
check("no leak when the card was just played in that action", "leak" not in kinds(clean(), ev))

ev = [{"type": "phase", "ts": 1.0}, {"type": "phase", "ts": 500.0}]
check("stall: a long gap with no game-over", "stall" in kinds(clean(), ev, stall_s=120))
check("no stall once the game is over", "stall" not in kinds(clean(), ev + [{"type": "game-over", "ts": 501}], stall_s=120))

b = clean(); b.insert(3, {"ts": 9.5, "seat": "A", "action": "cast", "body": {"name": "Odd Rite"}, "refused": "illegal", "why": "don't know how to play Odd Rite"})
check("unrunnable-text", "unrunnable-text" in kinds(b))

b = clean(); b.insert(3, {"ts": 9.5, "seat": "A", "action": "land", "body": {}, "refused": "illegal", "why": "Gruul Turf returns a land: say which with --bounce REF"})
fk = {f["kind"] for f in O.check_game([], b, TEXTS)}
check("a refusal for a missing choice is pilot-error, not a table bug", fk == {"pilot-error"}, fk)

s1 = O.signature("refused-but-offered", "cast", "Zorb Plains #12 costs 3, you have 1")
s2 = O.signature("refused-but-offered", "cast", "Quill Peak #99 costs 5, you have 2")
check("one bug, one signature (ids, numbers, names stripped)", s1 == s2, f"{s1} vs {s2}")

# every detector is load-bearing: with it off, its planted bug disappears
plants = {
    "refused-but-offered": ([{"ts": 1, "seat": "A", "action": "cast", "body": {}, "refused": "illegal", "why": "no"}], []),
    "leak": (clean(), [{"type": "say", "speaker": "A", "text": "Ashen Tome next", "ts": 5.0, "id": 1}]),
    "stall": (clean(), [{"type": "phase", "ts": 1.0}, {"type": "phase", "ts": 900.0}]),
}
_b = clean(); _b.insert(4, rec("A", "land", ["Quill Peak", "Ashen Tome", "Wren"], {"name": "Quill Peak"}))
plants["accepted-but-illegal"] = (_b, [])
_b = clean(); _b.insert(4, rec("A", "cast", ["Quill Peak", "Wren"], {"name": "Ashen Tome"}, drew=[]))
plants["no-effect"] = (_b, [])
_b = clean(); _b[4] = rec("A", "pass", ["Quill Peak", "Wren"], {"quiet": True})
plants["drift"] = (_b, [])
plants["unrunnable-text"] = ([{"ts": 1, "seat": "A", "action": "cast", "body": {}, "refused": "illegal", "why": "don't know how to play X"}], [])
for k, (br, evs) in plants.items():
    on = k in kinds(br, evs)
    off = k not in kinds(br, evs, only=[x for x in O.KINDS if x != k])
    check(f"mutation: {k} is caught when on and unseen when off", on and off, f"on={on} off={off}")

# a draw spell resolved by a LATER manual draw action of the same seat (host-style play) is not 'no effect'
b = clean(); b.insert(4, rec("A", "cast", ["Quill Peak", "Wren"], {"name": "Ashen Tome"}, drew=[]))
b.insert(5, rec("A", "draw", ["Quill Peak", "Wren", "X1", "X2"], {"n": 2}, drew=["X1", "X2"]))
check("no-effect looks ahead to the seat's following draw action", "no-effect" not in kinds(b))

# two records a few ms apart: the public line belongs to the LAND record even though a cast record is nearer in time
b = clean()
b[2] = rec("A", "land", ["Quill Peak", "Brindle Elf", "Ashen Tome", "Wren", "Zorb Plains"], {"name": "Zorb Plains", "bounce": "self"})
b[3] = rec("A", "cast", ["Quill Peak", "Ashen Tome", "Wren", "Zorb Plains"], {"name": "Brindle Elf"})
b[3]["ts"] = b[2]["ts"] + 0.01
ev = [{"type": "say", "speaker": "A", "text": "I play Zorb Plains, tapped.", "ts": b[3]["ts"], "id": 9, "action": "land"}]
check("a public line is matched to the record with its own action (no false leak)", "leak" not in kinds(b, ev))

ev = [{"type": "life", "player": "A", "delta": -3, "life": 2, "ts": 1.0, "id": 1}, {"type": "life", "player": "A", "delta": -3, "life": -1, "ts": 2.0, "id": 2},
      {"type": "life", "player": "A", "delta": -4, "life": -5, "ts": 3.0, "id": 3}]
check("after-elimination: a second hit in the SAME combat step (simultaneous damage) is legal, not flagged", not [f for f in O.check_game(ev, [], TEXTS) if f["kind"] == "after-elimination"])
ev2 = ev[:2] + [{"type": "phase", "player": "B", "step": "end step", "ts": 2.5, "id": 9}] + ev[2:]
fs = [f for f in O.check_game(ev2, [], TEXTS) if f["kind"] == "after-elimination"]
check("...damage after the step has moved on is flagged once, at the first such event", len(fs) == 1 and fs[0]["event_id"] == 3, fs)
check("...and the lethal hit itself is not", not [f for f in O.check_game(ev2[:2], [], TEXTS) if f["kind"] == "after-elimination"])
ev3 = [{"type": "life", "player": "A", "delta": -3, "life": -1, "ts": 1.0, "id": 1}, {"type": "phase", "ts": 1.5, "id": 2},
       {"type": "life", "player": "A", "delta": 2, "life": 1, "ts": 2.0, "id": 3}, {"type": "life", "player": "A", "delta": -4, "life": -3, "ts": 3.0, "id": 4}]
check("...a player back above 0 who drops again in the same step is not flagged", not [f for f in O.check_game(ev3, [], TEXTS) if f["kind"] == "after-elimination"])
b = clean(); b.insert(3, {"ts": 9.5, "seat": "A", "action": "cast", "body": {"name": "Bant Charm"}, "refused": "illegal", "why": "Bant Charm is modal \u2014 choose 1 with --mode (1-based): 1. x | 2. y"})
check("a modal-spell refusal is pilot-error", {f["kind"] for f in O.check_game([], b, TEXTS)} == {"pilot-error"})
b = clean()
for x in b: x["life"] = 34 if x["ts"] <= 4.0 else 32
ev = [{"type": "life", "player": "A", "delta": -6, "life": 34, "ts": 0.5, "id": 1}, {"type": "life", "player": "A", "delta": -2, "life": 32, "ts": 4.02, "id": 2}]
check("life drift tolerates an event stamped within 0.05 s of the record (either order)", "drift" not in {f["kind"] for f in O.check_game(ev, b, TEXTS)})
ev = [{"type": "life", "player": "A", "delta": -3, "life": 30, "ts": 0.5, "id": 1}]
check("...but still flags a record that matches no nearby life event", "drift" in {f["kind"] for f in O.check_game(ev, b, TEXTS)})

# --- battlefield checks (public snapshots) ---
TYPES = {"Brindle Elf": "Creature \u2014 Elf", "Ashen Tome": "Sorcery", "Zorb Plains": "Basic Land \u2014 Plains"}


def snap(ts, perms=(), hand=6, library=90, gy=0, cmdr="Cmdr"):
    return {"ts": ts, "seats": {"A": {"life": 40, "hand": hand, "library": library, "gy": gy, "cmdr": cmdr, "cmdr_in_zone": True,
                                     "perms": [[i + 1, n, "x", None, False, 0, tok, None] for i, (n, tok) in enumerate(perms)]}}}


def board(brain, snaps, **kw):
    return O.check_game([], brain, TEXTS, only=kw.get("only"), snaps=snaps, types=TYPES)


b = clean()                                    # the cast of Brindle Elf is the 4th record: ts 4.0
good = [snap(3.5), snap(4.5, [("Brindle Elf", False)])]
check("board: a cast creature that shows up is fine", not [f for f in board(b, good) if "appeared" in f["why"]])
bad = [snap(3.5), snap(4.5), snap(5.5)]
fs = [f for f in board(b, bad) if "appeared" in f["why"]]
check("board: a cast creature that never appears is a no-effect finding at its record", fs and fs[0]["kind"] == "no-effect" and fs[0]["brain_line"] == 3, fs)
check("board: ...unless a card visibly went to the graveyard (it died at once)", not [f for f in board(b, [snap(3.5), snap(4.5, gy=1)]) if "appeared" in f["why"]])
check("board: no sample after the play means no claim", not [f for f in board(b, [snap(3.5)]) if "appeared" in f["why"]])
check("board: a land played shows up as a permanent", [f for f in board(b, [snap(1.5), snap(3.2), snap(3.9)]) if "appeared" in f["why"] and f["action"] == "land"])
check("board: a sorcery is not expected on the battlefield", not [f for f in board(clean(), [snap(1.5), snap(5.5)]) if f["action"] == "cast" and "Tome" in str(f)])

base = snap(1.0, [], hand=7, library=92)       # 99 cards
dup1 = snap(2.0, [("Brindle Elf", False)], hand=7, library=92)   # one extra card
dup2 = snap(3.0, [("Brindle Elf", False)], hand=7, library=92)
check("board: zones holding more cards than the deck, twice in a row, is drift", [f for f in board([], [base, dup1, dup2]) if "more card" in f["why"]])
check("board: a single transient over-count is ignored (a card in flight)", not [f for f in board([], [base, dup1, snap(3.0, [], hand=7, library=92)]) if "more card" in f["why"]])
check("board: tokens and the commander on the battlefield are not counted", not [f for f in board([], [base, snap(2.0, [("Goblin", True), ("Cmdr", False)], hand=7, library=92), snap(3.0, [("Goblin", True), ("Cmdr", False)], hand=7, library=92)]) if "more card" in f["why"]])
for k, (brain_, snaps_) in {"no-effect": (b, bad), "drift": ([], [base, dup1, dup2])}.items():
    on_ = k in {f["kind"] for f in board(brain_, snaps_)}
    off_ = not [f for f in board(brain_, snaps_, only=[x for x in O.KINDS if x != k]) if f["kind"] == k and ("appeared" in f["why"] or "more card" in f["why"])]
    check(f"mutation: the board {k} detector is caught when on and unseen when off", on_ and off_, f"on={on_} off={off_}")

b = clean(); b.insert(3, {"ts": 9.5, "seat": "A", "action": "turn-up", "body": {}, "refused": "illegal", "why": "can't pay {4}{G} to turn #7 face up"})
check("an unaffordable turn-up refusal is pilot-error (the state offers no castable_now for it)", {f["kind"] for f in O.check_game([], b, TEXTS)} == {"pilot-error"})
O.check_game([], clean(), TEXTS, snaps=[snap(3.5), snap(4.5, [("Brindle Elf", False)])], types=TYPES)
check("board coverage is reported: plays seen vs plays actually checked", O.check_board.stats["plays"] >= 1 and O.check_board.stats["checked"] >= 1, O.check_board.stats)

# life drift when the first life event of the game shares the record's timestamp (no earlier event: the starting total is the baseline)
b = clean()
for x in b: x["life"] = 40
b.insert(3, rec("A", "block", ["Quill Peak", "Brindle Elf"], {"blocker": "#1"}, life=40)); b.insert(4, rec("A", "block", ["Quill Peak", "Brindle Elf"], {"blocker": None}, life=38))
b[3]["ts"] = b[4]["ts"] = 3.5
for x in b[5:]: x["life"] = 38; x["ts"] += 1
ev = [{"type": "life", "player": "A", "delta": -2, "life": 38, "ts": 3.5, "id": 1}]
check("life drift: the first life event of the game, stamped like the record, is not drift", "drift" not in {f["kind"] for f in O.check_game(ev, b, TEXTS)}, [f for f in O.check_game(ev, b, TEXTS) if f["kind"] == "drift"])
TYPES["Wild Growth"] = "Enchantment \u2014 Aura"
ba = clean(); ba[3] = rec("A", "cast", ["Quill Peak", "Ashen Tome", "Wren"], {"name": "Wild Growth", "on": "#5"})
check("board: an Aura cast is not expected as its own permanent", not [f for f in board(ba, [snap(3.5), snap(4.5), snap(5.5)]) if "appeared" in f["why"]])

# --- false-positive guards (each came from a real saved game) ---
ev = [{"type": "say", "speaker": "A", "text": "I play Zorb Plains.", "ts": 3.0, "id": 5, "action": "land"},
      {"type": "say", "speaker": "A", "text": "Ashen Tome untaps.", "ts": 5.5, "id": 6, "action": "untap"}]
check("no leak for a zone announcement about a permanent already out", "leak" not in kinds(clean(), ev))
ev = [{"type": "say", "speaker": "B", "text": "Ashen Tome is on the stack.", "ts": 4.0, "id": 4, "action": "cast"},
      {"type": "say", "speaker": "A", "text": "Ashen Tome again.", "ts": 5.0, "id": 6, "action": "say"}]
check("no leak for a name the table already heard", "leak" not in kinds(clean(), ev))
ev = [{"type": "say", "speaker": "A", "text": "I play Plains.", "ts": 3.0, "id": 5, "action": "land"}]
b = clean(); b[0]["hand_after"] = b[1]["hand_after"] = H0 + ["Plains"]
check("no leak for a basic land", "leak" not in kinds(b, ev))
b = clean(); b[3] = rec("A", "cast", ["Quill Peak", "Ashen Tome", "Wren"], {"name": "Elsha Prime"})
check("a commander cast is not 'card not in hand' when the deck names it", not [f for f in O.check_game([], b, TEXTS, commanders={"Elsha Prime"}) if f["kind"] == "accepted-but-illegal"])
check("...but the same cast IS flagged when no deck names it", [f for f in O.check_game([], b, TEXTS) if f["kind"] == "accepted-but-illegal"])
b = clean(); b.insert(4, rec("B", "take", ["Z1"], {"name": "Quill Peak", "from": "A"}))
b[5] = rec("A", "pass", ["Ashen Tome", "Wren"], {"quiet": True})
b[6] = rec("A", "end", ["Ashen Tome", "Wren"])
check("another seat's take does not read as hand drift", "drift" not in kinds(b))
b = clean(); b[2] = rec("A", "land", ["Q1", "Q2", "Q3", "Q4"], {"name": "Q1"})
b.insert(3, rec("A", "land", ["R1", "R2", "R3", "R4"], {"name": "R1"}))
check("a wholly different hand means a reset: no second-land or drift finding", not {"drift", "accepted-but-illegal"} & kinds(b))
import tempfile, json as _j
d = Path(tempfile.mkdtemp()) / "deck.json"
d.write_text(_j.dumps({"commander": [{"name": "Elsha Prime"}], "mainBoard": [
    {"name": "Ashen Tome", "type": "Sorcery", "text": "Draw two cards."},
    {"name": "Hydra", "type": "Creature", "text": "Whenever you cast a spell, draw a card."},
    {"name": "Vengeance", "type": "Sorcery", "text": "Destroy all.\nCycling {3} ({3}, Discard this card: Draw a card.)"}]}))
tx = O.card_texts([d])
check("only spells' own text is used for no-effect (not triggers, not cycling)", "Ashen Tome" in tx and "Hydra" not in tx and "draw" not in tx["Vengeance"].lower(), tx)
check("commanders come from a deck's commander list", O.commander_names([d]) == {"Elsha Prime"})

# --- spell effects on the public board: tokens and wipes ---
TEXTS2 = {**TEXTS, "Goblin Rally": "Create two 1/1 red Goblin creature tokens.", "Ruin Wave": "Destroy all creatures.", "Pick Fight": "Destroy target creature.", "Maybe Rally": "You may create a 1/1 Goblin creature token."}
TYPES2 = {**TYPES, "Old Wall": "Land", "Cogwork": "Artifact", "Goblin Rally": "Sorcery", "Ruin Wave": "Sorcery", "Pick Fight": "Sorcery", "Maybe Rally": "Sorcery"}


def spell_game(spell, snaps_):
    br = [rec("A", "cast", [], {"name": spell})]
    return [f for f in O.check_game([], br, TEXTS2, snaps=snaps_, types=TYPES2) if f["kind"] == "no-effect" and ("token" in f["why"] or "shrink" in f["why"])]


T[0] = 0.0
check("token spell: tokens appear -> silent", not spell_game("Goblin Rally", [snap(0.5), snap(1.5, [("Goblin", True), ("Goblin", True)])]))
T[0] = 0.0
check("token spell: no token appears -> no-effect", spell_game("Goblin Rally", [snap(0.5), snap(1.5), snap(2.5)]))
T[0] = 0.0
check("wipe: board does not shrink -> no-effect", spell_game("Ruin Wave", [snap(0.5, [("Brindle Elf", False)]), snap(1.5, [("Brindle Elf", False)])]))
T[0] = 0.0
check("wipe: board shrinks -> silent", not spell_game("Ruin Wave", [snap(0.5, [("Brindle Elf", False)]), snap(1.5)]))
T[0] = 0.0
T[0] = 0.0
check("wipe: only lands on the board and the spell hits creatures -> no claim", not spell_game("Ruin Wave", [snap(0.5, [("Old Wall", False)]), snap(1.5, [("Old Wall", False)])]))
check("wipe: nothing on the board to remove -> no claim", not spell_game("Ruin Wave", [snap(0.5), snap(1.5)]))
T[0] = 0.0
check("targeted and optional spells make no claim", not spell_game("Pick Fight", [snap(0.5, [("Brindle Elf", False)]), snap(1.5, [("Brindle Elf", False)])]) and not spell_game("Maybe Rally", [snap(0.5), snap(1.5)]))
T[0] = 0.0
check("mutation: board-spell detector is unseen when no-effect is off", not [f for f in O.check_game([], [rec("A", "cast", [], {"name": "Goblin Rally"})], TEXTS2, only=[k for k in O.KINDS if k != "no-effect"], snaps=[snap(0.5), snap(1.5)], types=TYPES2) if "token" in f["why"]])
sys.exit(0 if ok_all else 1)
