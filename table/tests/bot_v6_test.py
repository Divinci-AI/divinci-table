"""v6: the autopilot bot (review-20261109/bot-v6) answers the new kinds of question and never hangs a game: modes, "you may sacrifice", a target
player, pay-or-be-countered, and a spell held for its response.
    PYTHONDONTWRITEBYTECODE=1 python3 table/tests/bot_v6_test.py     (BOT_DIR: where claude_autopilot.py is)"""
import importlib, os, sys, tempfile, time
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
from pathlib import Path
from v6_harness import R, Table, check, run, cast, answer, choices, says, HERE
BOT = Path(os.path.expanduser(os.environ.get("BOT_DIR", str(HERE.parent / "bot-v6"))))
sys.path.insert(0, str(BOT))
cards_path.need(BOT / "claude_autopilot.py", "the Claude bot", "BOT_DIR=<dir with claude_autopilot.py>")
M, C = "Michael", "Claude"


def bot_for(t, seat):
    keyf = Path(tempfile.mkdtemp()) / "k"
    keyf.write_text(t.keys[seat]); keyf.chmod(0o600)
    os.environ.update(CLAUDE_SEAT=seat, CLAUDE_KEY_FILE=str(keyf), TABLE_URL=t.b)
    ap = importlib.import_module("claude_autopilot")
    ap.BASE, ap.SEAT, ap.KEY = t.b, seat, t.keys[seat]
    ap.REFUSED.clear()
    return ap


def questions():
    t = Table()
    try:
        t.start()
        ap = bot_for(t, M)
        t.lands(M, 12, "Mountain")
        t.onto(C, "Mugato")
        c, d = ap.do("cast", {"name": "Rogue Artificial Intelligence"}) if False else (0, {})
        l0 = t.lifeof(M)
        t.lands(M, 6, "Swamp")
        t.hand(M, "Rogue Artificial Intelligence")
        c, d = ap.do("cast", {"name": "Rogue Artificial Intelligence"})
        check(c == 200 and not choices(t, M) and t.lifeof(M) == l0 + 3, "a modal enters trigger: the bot picks a mode the table can do (You gain 3 life) and nothing is left waiting", (c, d.get("said") or d, choices(t, M)))
        t.turn(M)
        t.hand(M, "Dahar Master")
        n = len(t.state(M)["permanents"])
        c, d = ap.do("cast", {"name": "Dahar Master"})
        check(c == 200 and not choices(t, M) and len(t.state(M)["permanents"]) == n + 1, "Dahar Master with no token to give up: the bot declines the sacrifice", (c, d.get("said") or d, choices(t, M)))
        t.turn(M)
        t.call("POST", "/api/brain/token", {"seat": M, "name": "Borg", "power": 1, "toughness": 1, "quiet": True})
        t.hand(M, "Dahar Master")
        c, d = ap.do("cast", {"name": "Dahar Master"})
        check(c == 200 and not choices(t, M) and not any(p["name"].startswith("Borg") for p in t.state(M)["permanents"]) and t.perm(C, "Mugato")["damage"] == 2,
              "…with a token: it sacrifices the token and aims the 2 damage at the opponent's creature", (c, d.get("said") or d, choices(t, M), t.perm(C, "Mugato")))
        t.turn(M)
        t.lands(M, 4, "Island")
        t.hand(M, "Wesley Crusher, Starfleet Cadet")
        hc = len(t.state(C)["hand"])
        c, d = ap.do("cast", {"name": "Wesley Crusher, Starfleet Cadet"})
        for _ in range(4):
            ap.answer_choices()
        check(c == 200 and not choices(t, M), "Wesley's choose two: the bot answers every question it raises (modes, then their targets)", (c, d.get("said") or d, choices(t, M)))
    finally:
        t.stop()


def held():
    t = Table()
    try:
        t.start()
        ap = bot_for(t, C)                                  # the bot is the one who could respond
        t.lands(M, 9, "Forest"); t.lands(C, 6, "Island")
        t.hand(C, "Consider the Prime Directive"); t.hand(C, "Consider the Prime Directive")
        t.hand(M, "Tenacious Drone") if False else None
        t.lands(M, 2, "Swamp")
        cast(t, M, "Tenacious Drone")                      # mana value 2: not worth a counter
        ph = t.call("GET", "/api/phase")[1]
        check(ph.get("held") and ap.respond_to_held(ph) and t.perm(M, "Tenacious Drone") is not None and "held" not in t.call("GET", "/api/phase")[1],
              "a cheap spell held for the bot: it passes at once and the spell resolves", ph.get("held"))
        cast(t, M, "Mugato")                               # mana value 5: countered (unless paid)
        ph = t.call("GET", "/api/phase")[1]
        ok = ap.respond_to_held(ph)
        pend = choices(t, M)
        check(ok and pend and "Pay {3}" in pend[0]["slot"]["options"][0]["label"], "a five-mana spell: the bot casts the counter the table lists; Michael is asked to pay", (ok, pend))
        answer(t, M, "Don't pay")
        check(t.perm(M, "Mugato") is None and "Mugato" in (t.state(M).get("graveyard") or []), "…and it is countered", t.state(M).get("graveyard"))
        # the bot as the caster whose spell is countered-unless-paid: it pays when it can
        t.turn(C)
        ap2 = bot_for(t, C)
        t.hand(M, "Consider the Prime Directive")
        t.lands(M, 3, "Island")
        t.lands(C, 6, "Forest")
        t.hand(C, "Mugato")
        import threading
        res = {}
        th = threading.Thread(target=lambda: res.update(r=ap2.do("cast", {"name": "Mugato"})))
        th.start(); th.join(10)
        t.call("POST", "/api/brain/cast", {"seat": M, "name": "Consider the Prime Directive", "modes": [1], "targets": [{"at": C, "card": "Mugato"}]}, key=t.keys[M])
        for _ in range(3):
            ap2.answer_choices()
        check(t.perm(C, "Mugato") is not None and "held" not in t.call("GET", "/api/phase")[1], "the bot's own spell, countered unless it pays: it pays and the spell resolves", (res, choices(t, C)))
    finally:
        t.stop()


if __name__ == "__main__":
    run([("the bot answers modes, optional sacrifices, players", questions), ("the bot and a held spell", held)])
