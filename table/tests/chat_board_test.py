"""A person's typed or spoken play goes on THEIR public board (/board, /api/board3d) — the live bug: in a cloud room
Michael typed 'I play Silverbluff Bridge artifact land and pass', the log heard the play, and /board still said
"Nothing recorded yet." because only a brain or the person's own 'my board' call wrote PUBLIC_BOARD.

    ~/.venvs/table/bin/python table/tests/chat_board_test.py

Remote calls only (X-Forwarded-For on every request), cloud room (TABLE_CLOUD=1), ROUTER=code, no brain for the people:
the chat goes through /api/chat with the seat key, as the page's chat line sends it. One server on port 8831
(stage1_guard_test's Server): two pilots (Claude, Fusion), two people (Michael, Sam).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TABLE_HIGHROLL", "first")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage1_guard_test import Server  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {str(detail)[:300]}"), flush=True)


def say(S, by, text, key_of=None):
    """The page's chat line: {"by", "text"} with the seat key (assets/seat.js adds X-Seat-Key)."""
    return S.call("POST", "/api/chat", {"by": by, "text": text}, key=S.claim(key_of or by))


def board(S, seat):
    c, d = S.call("GET", "/api/board3d")
    assert c == 200, (c, d)
    s = next(x for x in d["seats"] if x["name"] == seat)
    return [p["name"] for p in s["permanents"]]


def main():
    with Server() as S:
        S.claim_all()

        print("before the game")
        c, d = say(S, "Michael", "I play Command Tower")
        check("the chat line is accepted", c == 200, (c, d))
        check("…but nothing goes on a board before the game starts", board(S, "Michael") == [], board(S, "Michael"))

        S.start_game()
        claude0 = board(S, "Claude")

        print("a person's own play")
        line = "I play Silverbluff Bridge artifact land and pass"
        c, d = say(S, "Michael", line)
        check("the play is heard, with its card", c == 200 and "Silverbluff Bridge" in (d.get("cards") or []), (c, d))
        check("Silverbluff Bridge is on Michael's board (/api/board3d)", board(S, "Michael") == ["Silverbluff Bridge"], board(S, "Michael"))
        c, d = S.call("GET", "/api/board3d")
        m = next(x for x in d["seats"] if x["name"] == "Michael")
        check("…as a public, untapped card with its type from the card file",
              m["permanents"][0].get("type") == "Artifact Land" and not m["permanents"][0].get("tapped") and m.get("updated"),
              m["permanents"])
        c, d = say(S, "Michael", line)
        check("the same announcement again does not add a second copy", c == 200 and board(S, "Michael") == ["Silverbluff Bridge"],
              board(S, "Michael"))
        say(S, "Michael", "I play Silverbluff Bridge")
        check("…nor does naming the same (non-basic) card in other words", board(S, "Michael") == ["Silverbluff Bridge"], board(S, "Michael"))

        print("not permanents, not targets")
        c, d = say(S, "Michael", "I cast Path to Exile")
        check("an instant is heard as a play", c == 200 and "Path to Exile" in (d.get("cards") or []), (c, d))
        check("…but does not go on the board", "Path to Exile" not in board(S, "Michael"), board(S, "Michael"))
        say(S, "Michael", "I cast Swords to Plowshares on Sam's Llanowar Elves")
        check("a spell's TARGET goes on nobody's board",
              "Llanowar Elves" not in board(S, "Michael") + board(S, "Sam") and "Swords to Plowshares" not in board(S, "Michael"),
              (board(S, "Michael"), board(S, "Sam")))

        print("only your own board")
        before = board(S, "Michael")
        c, d = say(S, "Michael", "I play Sol Ring", key_of="Sam")
        check("Sam cannot speak as Michael (the seat guard refuses)", c == 403, (c, d))
        check("…and Michael's board is unchanged", board(S, "Michael") == before, board(S, "Michael"))
        c, d = say(S, "Sam", "I play Forest")
        check("Sam's own play goes on Sam's board", board(S, "Sam") == ["Forest"], board(S, "Sam"))
        check("…and not on Michael's", board(S, "Michael") == before, board(S, "Michael"))
        say(S, "Sam", "I play Forest")
        check("a basic land said twice is one land", board(S, "Sam") == ["Forest"], board(S, "Sam"))
        say(S, "Sam", "I play Forest again")
        check("…a second basic in a new announcement is a second land", board(S, "Sam") == ["Forest", "Forest"], board(S, "Sam"))

        print("pilot seats are the engine's")
        c, d = say(S, "Claude", "I play Sol Ring")
        check("a pilot's chat play does not write its board (the engine owns it)", board(S, "Claude") == claude0, (claude0, board(S, "Claude")))

        print("a correction replaces the misheard card")
        say(S, "Michael", "I play Command Tower")
        check("Command Tower is on Michael's board", "Command Tower" in board(S, "Michael"), board(S, "Michael"))
        c, d = say(S, "Michael", "No, I said Exotic Orchard")
        b = board(S, "Michael")
        check("'No, I said Exotic Orchard' takes Command Tower off and puts Exotic Orchard on",
              "Command Tower" not in b and "Exotic Orchard" in b and b.count("Silverbluff Bridge") == 1, (c, d, b))

    pilot_records()
    print(f"\n{PASS} passed, {FAIL} failed")


def tablectl(S, key_file, *argv):
    """tablectl as the cloud pilot runs it: its seat key, NO host token file, looking like a remote device."""
    import subprocess
    env = {**os.environ, "TABLE_URL": "http://127.0.0.1:8831", "TABLE_SEAT": "Claude", "TABLE_SEAT_KEY_FILE": str(key_file),
           "TABLE_TOKEN_FILE": str(S.tmp / "no-such-token"), "TABLE_FORWARD_FOR": "203.0.113.9"}
    p = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "tablectl.py"), *argv],
                       env=env, capture_output=True, text=True, timeout=60)
    return p.returncode, (p.stdout + p.stderr).strip()


def pilot_records():
    """The second live case: Sam posted a photo of River of Tears and typed 'played a land' (no name). A cloud room has no
    vision; a pilot seat can read the photo and record the card on Sam's board with its own seat key."""
    print("a pilot records a person's board")
    with Server() as S:
        S.claim_all()
        S.start_game()
        pb = lambda body, key=None, **kw: S.call("POST", "/api/public-board", body, key=key, **kw)

        key_file = S.tmp / "claude-seat-key"
        key_file.write_text(S.claim("Claude"))
        code, out = tablectl(S, key_file, "public-board", "Sam", "River of Tears", "--from", "photo")
        check("tablectl public-board with a PILOT's seat key and no host token file is accepted", code == 0 and '"ok": true' in out, out)
        check("River of Tears is on Sam's board (/api/board3d)", board(S, "Sam") == ["River of Tears"], board(S, "Sam"))
        c, d = S.call("GET", "/api/board3d")
        sam = next(x for x in d["seats"] if x["name"] == "Sam")
        check("…marked as Claude's record, from a photo", sam["permanents"][0].get("by") == "Claude" and sam["permanents"][0].get("from") == "photo",
              sam["permanents"])
        ev = S.call("GET", "/api/events?since=0")[1]["events"]
        line = [e for e in ev if e.get("type") == "chat" and e.get("recorded_for") == "Sam"]
        check("the game log says so: 'Claude recorded Sam's River of Tears from Sam's photo'",
              line and "Claude recorded Sam's River of Tears from Sam's photo" in line[-1]["text"] and line[-1]["by"] == "Claude", line)
        c, d = pb({"seat": "Sam", "permanents": ["River of Tears"]}, key=S.claim("Claude"))
        check("the same card again is not added twice", c == 200 and board(S, "Sam") == ["River of Tears"], (c, d, board(S, "Sam")))
        c, d = pb({"seat": "Sam", "permanents": ["Swamp"], "graveyard": ["Murder"]}, key=S.claim("Fusion"))
        check("a pilot ADDS (never replaces): Fusion's Swamp goes beside River of Tears, Murder to the graveyard",
              c == 200 and board(S, "Sam") == ["River of Tears", "Swamp"] and sam_gy(S) == ["Murder"], (c, d, board(S, "Sam"), sam_gy(S)))

        print("corrections: a pilot can take back what IT recorded")
        c, d = pb({"seat": "Sam", "remove": ["Swamp"]}, key=S.claim("Claude"))
        check("Claude cannot remove Swamp: Fusion recorded it, not Claude", c == 200 and board(S, "Sam") == ["River of Tears", "Swamp"], (c, d, board(S, "Sam")))
        c, d = pb({"seat": "Sam", "remove": ["River of Tears"]}, key=S.claim("Claude"))
        check("Claude removes its own River of Tears (recorded in error)", c == 200 and board(S, "Sam") == ["Swamp"] and d.get("removed") == ["River of Tears"], (c, d, board(S, "Sam")))
        ev = S.call("GET", "/api/events?since=0")[1]["events"]
        check("…and the log says it took it off", any(e.get("removed") == ["River of Tears"] and "off Sam's board" in e.get("text", "") for e in ev), [e.get("text") for e in ev if e.get("type") == "chat"][-3:])
        pb({"seat": "Sam", "permanents": ["River of Tears"]}, key=S.claim("Claude"))
        c, d = pb({"seat": "Sam", "remove": ["River of Tears"]}, key=S.claim("Michael"))
        check("a person's key cannot use the removal route", c == 403, (c, d))

        print("…only a person's board, only with a pilot's key")
        claude0, fusion0 = board(S, "Claude"), board(S, "Fusion")
        c, d = pb({"seat": "Fusion", "permanents": ["Sol Ring"]}, key=S.claim("Claude"))
        check("a pilot cannot write ANOTHER pilot's board", c == 403 and board(S, "Fusion") == fusion0, (c, d))
        c, d = pb({"seat": "Claude", "permanents": ["Sol Ring"]}, key=S.claim("Claude"))
        check("…nor its own (the engine owns it)", c == 403 and board(S, "Claude") == claude0, (c, d))
        mich0 = board(S, "Michael")
        c, d = pb({"seat": "Michael", "permanents": ["Sol Ring"]}, key=S.claim("Sam"))
        check("a PERSON's key cannot use the pilot route for someone else", c == 403 and board(S, "Michael") == mich0, (c, d))
        c, d = pb({"seat": "Sam", "permanents": ["Sol Ring"]}, key=S.claim("Sam"))
        check("…nor for their own board (that is /api/my-board)", c == 403 and "Sol Ring" not in board(S, "Sam"), (c, d))
        c, d = pb({"seat": "Sam", "permanents": ["Sol Ring"]})
        check("no key, no token: 403", c == 403 and "Sol Ring" not in board(S, "Sam"), (c, d))
        c, d = pb({"seat": "Sam", "permanents": ["Sol Ring"], "key": "not-a-real-key"})
        check("a made-up key: 403", c == 403 and "Sol Ring" not in board(S, "Sam"), (c, d))
        c, d = pb({"seat": "Sam", "permanents": ["Sol Ring"]}, host=True)
        check("the host's brain token still REPLACES a board, as before", c == 200 and board(S, "Sam") == ["Sol Ring"], (c, d, board(S, "Sam")))
        pb({"seat": "Sam", "permanents": ["River of Tears"]}, key=S.claim("Claude"))

        print("the person's own board wins")
        c, d = S.call("POST", "/api/my-board", {"by": "Sam", "key": S.claim("Sam"), "permanents": ["Watery Grave"], "graveyard": []},
                      key=S.claim("Sam"))
        c2, d2 = S.call("GET", "/api/board3d")
        sam = next(x for x in d2["seats"] if x["name"] == "Sam")
        check("Sam's /api/my-board overwrite replaces the pilot's record", c == 200 and [p["name"] for p in sam["permanents"]] == ["Watery Grave"]
              and not sam["permanents"][0].get("by"), (c, d, sam["permanents"]))


def sam_gy(S):
    c, d = S.call("GET", "/api/board3d")
    return next(x for x in d["seats"] if x["name"] == "Sam")["graveyard"]
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
