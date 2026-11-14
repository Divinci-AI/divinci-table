"""2026-11-13, item 6 (the table's side): reading a whole card before choosing it, without learning anything one was not shown.

The owner hit "YOUR CHOICE … Claude's hand (7): … Choose the nonland card to exile" (Stray from History) with options that were only
`Name {cost} (Type P/T)`. The page now shows the whole card beside an option. What it reads, and what this file pins on a real table:

  * the cards of a hand a seat was SHOWN (its own pending question's `reveal.cards`) now carry their type line and rules text. That view is
    in the chooser's own state and nowhere else: not in the other seat's state, not on the public board, not in the log, not in /api/phase,
    not in the delayed spectator feed;
  * /api/card?name=… (public, as before) now also answers for a card of the set being played that the Oracle bulk file does not have, from the
    set's own card file. It is a lookup by NAME in static files: it answers the same for a card that sits in a hidden hand, a card in nobody's
    deck, and whether or not a game is on; it has no seat, zone or owner in it, so it cannot be used to find out what is in a hidden zone.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/retro_1113_peek_test.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import cards_path  # noqa: E402
import retro_game4_test as R  # noqa: E402

check, CARDS = R.check, R.CARDS
M, C = "Michael", "Claude"
STRAY = "Stray from History"
R.POOL.update({STRAY: 6, "Moopsy": 6, "Horta": 6, "Plasma Cascade": 6})
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in (8800, 8801, 8820, 8830, 8883, 8891))   # never a live game's port
R.DECK_MICHAEL = R.deck_file("Michael-peek")
R.DECK_CLAUDE = R.deck_file("Claude-peek")
os.environ["TABLE_CARDS_JSON"] = cards_path.cards_json()          # (the table is told where the set's card file is, as a launcher would)
SET = {c["name"]: c for c in json.load(open(cards_path.cards_json())) if c.get("set") == "trk"}


def lookup(t, name):
    try:
        with urllib.request.urlopen(t.b + "/api/card?name=" + urllib.parse.quote(name), timeout=10) as x:     # no key, no token: it is public
            return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main_():
    t = R.Table()
    try:
        t.start()                                            # Michael's main 1
        import oracle
        outside = sorted(n for n in SET if n not in R.POOL and oracle.card(n) is None and "//" not in n)          # in nobody's deck here, and not in the Oracle file
        inside = sorted(n for n in R.POOL if n in SET and oracle.card(n) is None)                                  # in the decks here, and not in the Oracle file

        print("\n── the public card lookup")
        c, d = lookup(t, "Horta")
        check(c == 200 and sorted(d) == ["cost", "name", "power", "text", "toughness", "type"], "it answers with a card's printed text and nothing else (name, cost, type, text, power, toughness)", (c, d))
        if outside and inside:
            a, b = outside[0], inside[0]
            (ca, da), (cb, db) = lookup(t, a), lookup(t, b)
            check(ca == 200 and cb == 200 and sorted(da) == sorted(db) == ["cost", "name", "power", "text", "toughness", "type"],
                  f"a set card the Oracle file lacks is answered from the set's own file, the same for one in NO deck here ({a}) and one in the decks ({b})", (ca, cb, da, db))
            check(da["text"] == (SET[a].get("oracle_text") or "") and da["type"] == SET[a].get("type_line") and da["cost"] == (SET[a].get("mana_cost") or ""), "…and it is that card's printed text", da)
        else:
            check(True, "(every set card is in the Oracle file on this machine: the set-file fallback had nothing to answer)")
        c, d = lookup(t, "No Such Card At All")
        check(c == 404, "a name that is no card: 404", (c, d))

        print("\n── a hand is shown to one seat")
        for n in ("Swamp", "Swamp", "Swamp"):
            t.onto(M, n)
        for n in ("Horta", "Moopsy", "Plasma Cascade", "Forest"):
            t.call("POST", "/api/brain/search", {"seat": C, "name": n})
        claude_hand = {h["name"] for h in t.state(C)["hand"]}
        t.hand(M, STRAY)
        acts = [a for a in t.legal(M)["actions"] if a["action"] == "cast" and a["body"].get("name") == STRAY]
        act = next((a for a in acts if "player" in json.dumps(a["body"])), acts[-1] if acts else None)
        assert act, acts
        ev0 = max([e.get("id", 0) for e in t.events()] or [0])
        c, d = t.act(M, "cast", **{k: v for k, v in act["body"].items() if k != "seat"})
        pend = (t.state(M).get("pending_choices") or [None])[0]
        check(c == 200 and pend and pend.get("reveal"), "Michael casts Stray from History at Claude and is asked which nonland card to exile", (c, d, pend))
        cards = {x["name"]: x for x in pend["reveal"]["cards"]}
        check(set(cards) == claude_hand, "the question holds exactly Claude's hand", (sorted(cards), sorted(claude_hand)))
        check(all(x.get("type") and "text" in x for x in cards.values()) and "Trample" in cards["Horta"]["text"] and "Deathtouch" in cards["Moopsy"]["text"]
              and cards["Horta"]["type"] == CARDS["Horta"]["type"], "each card in it now carries its type line and full rules text (what the page shows beside the option)", cards.get("Horta"))
        check(cards["Horta"]["cost"] == "{4}{R}{R}" and cards["Horta"]["power"] == "7" and cards["Horta"]["land"] is False and cards["Forest"]["land"] is True, "…and what it carried before is unchanged (cost, power, land)", cards["Horta"])
        labels = [o["label"] for o in pend["slot"]["options"]]
        check(any(l.startswith("Horta {4}{R}{R}") for l in labels) and not any(l.startswith("Forest") for l in labels), "the options themselves are as before (a land is not one)", labels)

        print("\n── …and to nobody else")
        hidden = [n for n in claude_hand if n not in ("Forest",)]
        texts = ["This creature gains reach until end of turn", "deals 3 damage to any target", "Basic landcycling"]
        everywhere = {
            "Claude's own state (its pending questions)": json.dumps(t.state(C).get("pending_choices") or []),
            "the public board": json.dumps(t.call("GET", "/api/board3d", host=False)[1]),
            "the log since the cast": json.dumps([e for e in t.events() if e.get("id", 0) > ev0]),
            "/api/phase": json.dumps(t.call("GET", "/api/phase", host=False)[1]),
            "the spectator feed": json.dumps(t.call("GET", "/api/spectate", host=False)[1]),
            "the legal list of the OTHER seat": json.dumps(t.legal(C)),
        }
        for where, blob in everywhere.items():
            check(not any(x in blob for x in texts) and '"reveal"' not in blob, f"no card text from that hand, and no reveal, in {where}", [x for x in texts if x in blob])
        pub = everywhere["the log since the cast"] + everywhere["the public board"] + everywhere["/api/phase"] + everywhere["the spectator feed"]
        check(not any(n in pub for n in ("Plasma Cascade", "Moopsy")), "the public side does not name the cards in the hand either (only that Michael looked)", [n for n in hidden if n in pub])
        check("looks at Claude's hand" in everywhere["the log since the cast"], "the log says that Michael looked")
        c2, d2 = lookup(t, "Moopsy")
        t.call("POST", "/api/brain/choose", {"seat": M, "id": pend["id"], "target": {k: v for k, v in pend["slot"]["options"][0].items() if k != "label"}}, key=t.keys[M])
        c3, d3 = lookup(t, "Moopsy")
        check((c2, d2) == (c3, d3) == lookup(t, "Moopsy"), "the lookup's answer for a card does not depend on where a copy of it is (asked while it was in the hidden hand, and after)")
    finally:
        t.stop()


def main():
    try:
        main_()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        check(False, "the test ran to its end", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
