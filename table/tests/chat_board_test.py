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

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
