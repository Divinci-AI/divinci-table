"""T16: "This creature can't attack alone" (Pet Sehlat) is the engine's rule, not a note: the attack is refused, the legal list and the
state's per-creature can_attack / why_not agree with it (so the page's Attack button and its "N ready" count do too), and the bot follows.

    PYTHONDONTWRITEBYTECODE=1 ~/.venvs/table/bin/python table/tests/alone_test.py"""
import importlib, os, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
BOT = Path(os.path.expanduser(os.environ.get("BOT_DIR", str(HERE.parent / "bot-v6"))))
sys.path[:0] = [str(HERE), str(HERE / "tests"), str(BOT)]
import retro_game4_test as R
import player
check = R.check


def legal(t, seat):
    return t.call("GET", f"/api/seat/legal?seat={seat}", key=t.keys[seat])[1]


def unit():
    v = player.VirtualPlayer(R.DECK_MICHAEL, "Michael", seed=2)
    v.turn = 3
    mk = lambda name, text="", **kw: player.Perm({"name": name, "types": ["Creature"], "power": "2", "toughness": "2", "text": text}, **kw)
    sehlat = mk("Pet Sehlat", "This creature can't attack alone.", sick=False)
    wall = mk("Wall", "Defender", sick=False)
    wall.card["keywords"] = ["Defender"]
    new = mk("New Guy")                                   # summoning sick
    tapped = mk("Tapped Guy", sick=False, tapped=True)
    stunned = mk("Stunned Guy", sick=False, tapped=True, stun=1)
    land = player.Perm({"name": "Forest", "types": ["Land"], "text": ""})
    v.battlefield += [sehlat, wall, new, tapped, stunned, land]
    r = v.attack_report()
    check(land.id not in r, "a land has no attack entry", r.get(land.id))
    check(r[new.id] == {"can_attack": False, "why_not": "summoning sick: it came under your control this turn", "needs_partner": False}, "summoning sick: not ready, and why", r[new.id])
    check(r[tapped.id]["why_not"] == "tapped" and "stun counter" in r[stunned.id]["why_not"], "tapped, and tapped under a stun counter, say so", (r[tapped.id], r[stunned.id]))
    check(r[wall.id]["why_not"] == "it has defender", "defender says so", r[wall.id])
    check(not r[sehlat.id]["can_attack"] and "can't attack alone" in r[sehlat.id]["why_not"], "can't attack alone, nothing else can attack: not ready", r[sehlat.id])
    try:
        v.manual_attack({f"#{sehlat.id}": "Claude"})
        check(False, "the engine refuses the lone attack")
    except player.IllegalAction as e:
        check("can't attack alone" in str(e) and not sehlat.tapped and sehlat.attacked_turn != v.turn, "the engine refuses the lone attack; nothing was tapped or marked", e)
    new.sick = False
    r = v.attack_report()
    check(r[sehlat.id] == {"can_attack": True, "why_not": None, "needs_partner": True} and r[new.id]["can_attack"], "with a partner that can attack it is ready (and marked as needing one)", r[sehlat.id])
    try:
        v.manual_attack({f"#{sehlat.id}": "Claude"})
        check(False, "still refused alone when a partner exists but isn't declared")
    except player.IllegalAction:
        check(not sehlat.tapped, "still refused alone when a partner exists but isn't declared", "")
    said = v.manual_attack({f"#{sehlat.id}": "Claude", f"#{new.id}": "Claude"})
    check(sehlat.tapped and new.tapped and any("I attack Claude" in x for x in said), "together they attack", said)
    s2 = mk("Pet Sehlat", "This creature can't attack alone.", sick=False)
    v.battlefield.append(s2)
    check(v.attack_report()[s2.id]["can_attack"], "a second one may join an attack already declared this turn", v.attack_report()[s2.id])
    v.manual_attack({f"#{s2.id}": "Claude"})
    check(s2.tapped, "…and the engine takes it", "")
    w = player.VirtualPlayer(R.DECK_MICHAEL, "Michael", seed=2)
    a, b = mk("Pet Sehlat", "This creature can't attack alone.", sick=False), mk("Pet Sehlat", "This creature can't attack alone.", sick=False)
    w.battlefield += [a, b]
    check(all(x["can_attack"] for x in w.attack_report().values()), "two of them can attack together", w.attack_report())
    fd = mk("Pet Sehlat", "This creature can't attack alone.", sick=False, face_down=True)
    check(not player.attacks_alone_banned(fd), "a face-down one has no such text", "")
    blk = mk("Shy", "This creature can't block alone.")
    both = mk("Shyer", "This creature can't attack or block alone.")
    check(player.blocks_alone_banned(blk) and player.blocks_alone_banned(both) and player.attacks_alone_banned(both) and not player.attacks_alone_banned(blk)
          and not player.blocks_alone_banned(sehlat), "the block-alone and attack-or-block-alone wordings are read", "")


def table():
    t = R.Table()
    try:
        t.start()
        t.onto("Michael", "Pet Sehlat")
        t.onto("Claude", "Mugato")
        t.turn("Michael")                                  # a new turn: the Sehlat is ready, the creature put now is not
        cap = t.onto("Michael", "Horta")
        st = t.state("Michael")
        sehlat = next(p for p in st["permanents"] if p["name"] == "Pet Sehlat")
        horta = next(p for p in st["permanents"] if p["name"] == "Horta")
        check(sehlat["can_attack"] is False and "can't attack alone" in (sehlat["why_not"] or ""), "state: the Sehlat is not ready, and says why", sehlat.get("why_not"))
        check(horta["can_attack"] is False and "summoning sick" in (horta["why_not"] or ""), "state: the new creature is summoning sick", horta.get("why_not"))
        check(sum(1 for p in st["permanents"] if p.get("pt") and p["can_attack"]) == 0, "so the page's count of ready creatures is 0 (it was 1)", "")
        la = legal(t, "Michael")
        check(not any(a["action"] == "attack" for a in la["actions"]), "the legal list offers no attack", [a["action"] for a in la["actions"]])
        c, d = t.act("Michael", "attack", assign={f"#{sehlat['id']}": "Claude"})
        check(c == 400 and "can't attack alone" in d.get("error", "") and not t.perm("Michael", "Pet Sehlat")["tapped"], "the attack is refused with the reason; nothing happened", (c, d))
        # the bot: no lone attack, and its turn still ends
        keyf = Path(tempfile.mkdtemp()) / "k"
        keyf.write_text(t.keys["Michael"]); keyf.chmod(0o600)
        os.environ.update(CLAUDE_SEAT="Michael", CLAUDE_KEY_FILE=str(keyf), TABLE_URL=t.b)
        ap = importlib.import_module("claude_autopilot")
        ap.BASE, ap.SEAT, ap.KEY = t.b, "Michael", t.keys["Michael"]
        seen = len(t.events(0))
        ap.play_turn()
        ev = t.events(0)[seen:]
        check(not any(e.get("type") == "say" and "attack" in (e.get("text") or "").lower() and e.get("speaker") == "Michael" for e in ev)
              and not any("can't attack alone" in str(e) for e in ev), "the bot doesn't try the lone attack", [e.get("text") for e in ev][-6:])
        t.turn("Michael")
        st = t.state("Michael")
        sehlat = next(p for p in st["permanents"] if p["name"] == "Pet Sehlat")
        check(sehlat["can_attack"] and sehlat["attack_needs_partner"] and sehlat["why_not"] is None, "next turn, with a partner ready: the Sehlat is ready (with a partner)", sehlat)
        la = legal(t, "Michael")
        atk = next((a for a in la["actions"] if a["action"] == "attack"), None)
        check(atk and f"#{sehlat['id']}" in atk["body"]["assign"] and len(atk["body"]["assign"]) >= 2, "the legal list offers it together with the other creature", atk)
        c, d = t.act("Michael", "attack", assign={f"#{sehlat['id']}": "Claude"})
        check(c == 400 and "can't attack alone" in d.get("error", ""), "alone it is still refused", (c, d))
        c, d = t.act("Michael", "attack", assign=atk["body"]["assign"])
        check(c == 200 and t.perm("Michael", "Pet Sehlat")["tapped"], "together they attack", (c, d.get("said") or d))
    finally:
        t.stop()


if __name__ == "__main__":
    for title, fn in (("the rule, on the engine", unit), ("at a table", table)):
        print("\n" + title, flush=True)
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(False, f"{title}: raised", e)
    print(("\nFAILED: " + "; ".join(R.FAILS)) if R.FAILS else "\nall good", flush=True)
    sys.exit(1 if R.FAILS else 0)
